"""Přerušení dlouhé práce na žádost hostitele (`lib/abort.py`, 2026-09-16).

Kodi při `Application.Quit` čeká na doběhnutí skriptů doplňku — a jádro dřív
nikde nekontrolovalo, jestli nemá přestat, takže vypnutí TV boxu trvalo minuty.
Testy tu jsou bez sítě: zdroje se podstrkují, čas se měří.

    python3 -m unittest tests.test_abort -v
"""
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core import Aborted, Engine                       # noqa: E402
from nokturno_core.lib import abort                              # noqa: E402
from nokturno_core.lib.sosac_direct import SosacDirect           # noqa: E402
from nokturno_core.lib.storage_api import StorageApi             # noqa: E402


def stop_after(n):
    """`should_stop`, které po `n` dotazech začne říkat ano — a počítá, kolikrát se ho kdo ptal."""
    calls = [0]

    def should_stop():
        calls[0] += 1
        return calls[0] > n
    should_stop.calls = calls
    return should_stop


class TestAborted(unittest.TestCase):
    def test_projde_pres_except_exception(self):
        """Jádro má všude „výpadek zdroje nesmí shodit ostatní" (`except Exception`) —
        přerušení má naopak projít úplně vším, jako KeyboardInterrupt."""
        self.assertTrue(issubclass(Aborted, BaseException))
        self.assertFalse(issubclass(Aborted, Exception))

    def test_check(self):
        abort.check(None)                    # bez callbacku se nikdy nepřeruší
        abort.check(lambda: False)
        with self.assertRaises(Aborted):
            abort.check(lambda: True)


class TestGather(unittest.TestCase):
    def test_bez_preruseni_vrati_vsechno_v_poradi(self):
        pool = ThreadPoolExecutor(max_workers=2)
        futures = [pool.submit(lambda i=i: i * 10) for i in range(5)]
        done = []
        out = abort.gather(pool, futures, abort.never, on_done=lambda f: done.append(f.result()), poll=0.05)
        self.assertEqual([f.result() for f in out], [0, 10, 20, 30, 40])
        self.assertEqual(sorted(done), [0, 10, 20, 30, 40])
        self.assertTrue(pool._shutdown)

    def test_preruseni_zrusi_nezacate_a_neceka_na_bezici(self):
        """Šest úloh po 2 s ve dvou vláknech = 6 s. Přerušení hned po startu:
        `gather()` vyhodí `Aborted` do zlomku vteřiny, čtyři nezačaté zruší a na
        dvě rozběhnuté nečeká (executor zavře bez čekání)."""
        started = []
        release = threading.Event()

        def job(i):
            started.append(i)
            release.wait(2)
            return i
        pool = ThreadPoolExecutor(max_workers=2)
        futures = [pool.submit(job, i) for i in range(6)]
        t0 = time.time()
        with self.assertRaises(Aborted):
            abort.gather(pool, futures, lambda: True, poll=0.05)
        self.assertLess(time.time() - t0, 1.0, "nesmí čekat na rozběhnutá vlákna")
        self.assertEqual(sum(1 for f in futures if f.cancelled()), 4)
        self.assertEqual(len(started), 2)
        release.set()
        pool.shutdown(wait=True)

    def test_preruseni_az_v_prubehu(self):
        """Dokud `should_stop()` mlčí, čeká se dál; hotové úlohy dostanou `on_done`."""
        pool = ThreadPoolExecutor(max_workers=1)
        futures = [pool.submit(time.sleep, 0.1) for _ in range(20)]
        done = []
        should_stop = stop_after(3)
        with self.assertRaises(Aborted):
            abort.gather(pool, futures, should_stop, on_done=done.append, poll=0.05)
        self.assertGreaterEqual(len(done), 1, "první úlohy stihly doběhnout a ohlásit se")
        self.assertLess(len(done), 20)
        pool.shutdown(wait=True)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def cache_files(self):
        return sorted(p.name for p in pathlib.Path(self.dir).glob("cache/*.json"))

    def test_vychozi_je_nikdy(self):
        """HA a Stremio `should_stop` nepředávají — jádro se tam nesmí přerušit nikdy."""
        engine = Engine({}, self.dir)
        self.assertIs(engine.should_stop, abort.never)
        engine._check_stop()

    def test_raw_streams_se_prerusi_a_nic_necachuje(self):
        engine = Engine({}, self.dir, should_stop=lambda: True)
        before = self.cache_files()
        t0 = time.time()
        with self.assertRaises(Aborted):
            engine.raw_streams("movie", "tt0133093", meta_video=({"name": "Matrix", "year": 1999}, None))
        self.assertLess(time.time() - t0, 3.0)
        self.assertEqual(self.cache_files(), before, "přerušený výsledek se nesmí zapsat do cache")

    def test_raw_streams_bez_preruseni_funguje(self):
        """Stejná cesta bez `should_stop` — bez zdrojů vrátí prázdno, nic nespadne."""
        engine = Engine({}, self.dir)
        self.assertEqual(engine.raw_streams("movie", "tt0133093",
                                            meta_video=({"name": "Matrix", "year": 1999}, None)), [])

    def test_fill_audio_prerusi_cteni_hlavicek(self):
        """Čtení hlaviček je nejdelší fáze (až 24 souborů) — přeruší se mezi soubory."""
        should_stop = stop_after(2)
        engine = Engine({"audio_probe": "24"}, self.dir, should_stop=should_stop)
        probed = []
        release = threading.Event()

        def media(url):
            probed.append(url)
            release.wait(0.3)
            return {}
        engine._media_from_file = media
        streams = [{"url": f"hs:{i}", "label": f"soubor {i}", "detail": ""} for i in range(24)]
        with self.assertRaises(Aborted):
            engine._fill_audio(streams)
        release.set()
        self.assertLess(len(probed), 24, "nezačaté hlavičky se zrušily")

    def test_klienti_dostanou_should_stop(self):
        """Sosáč (index seriálů po písmenech) a úložiště (průchod stromu) mají vlastní
        smyčky — jádro jim svůj `should_stop` musí podstrčit."""
        stop = lambda: False  # noqa: E731
        engine = Engine({"streamuj_username": "u", "streamuj_password": "p",
                         "dav1_url": "http://nas.lan/dav/"}, self.dir, should_stop=stop)
        self.assertIs(engine.sosac.should_stop, stop)
        self.assertIs(engine.sosac_db.should_stop, stop)
        self.assertIs(engine.storages[0].should_stop, stop)


class TestKlienti(unittest.TestCase):
    def test_uloziste_prerusi_pruchod_mezi_vrstvami(self):
        """Nekonečný strom (každá složka má dvě podsložky) — bez přerušení by se šlo
        až na `MAX_DIRS`; s ním skončí hned po první vrstvě."""
        listed = []

        def list_dir_safe(rel):
            listed.append(rel)
            return [(f"{rel}/{rel or 'k'}{i}", True, 0) for i in range(2)]
        api = StorageApi("http://nas.lan/dav/", should_stop=stop_after(1))
        api._list_dir_safe = list_dir_safe
        with self.assertRaises(Aborted):
            api.index()
        self.assertLessEqual(len(listed), 1 + 2, "nejvýš kořen a jedna vrstva")

    def test_uloziste_bez_preruseni_projde(self):
        def list_dir_safe(rel):
            if not rel:
                return [("Filmy", True, 0)]
            return [(f"{rel}/film.mkv", False, 10)]
        api = StorageApi("http://nas.lan/dav/")
        api._list_dir_safe = list_dir_safe
        self.assertEqual([f["path"] for f in api.index()["files"]], ["Filmy/film.mkv"])

    def test_sosac_prerusi_index_serialu(self):
        api = SosacDirect(should_stop=lambda: True)
        api._get = lambda *a, **k: self.fail("před prvním dotazem se má přerušit")
        with self.assertRaises(Aborted):
            api._series_index()


if __name__ == "__main__":
    unittest.main()
