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
from unittest import mock
import unittest.mock
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

    def test_on_done_muze_ukoncit_cekani_drive(self):
        """Vrátí-li `on_done` pravdu, `gather()` skončí — bez `Aborted`, nezačaté úlohy
        se zruší a na pomalou rozběhnutou se nečeká. Jediný pracovník: po rychlé úloze
        se pustí do pomalé (nezávisle na `gather()`), ale na `nezacaty` ve frontě
        se ještě nedostal, a ta se tedy dá zrušit."""
        release = threading.Event()
        started_slow = threading.Event()
        pool = ThreadPoolExecutor(max_workers=1)
        fast = pool.submit(lambda: "rychlý")

        def slow_job():
            started_slow.set()
            release.wait(5)
            return "pomalý"
        slow = pool.submit(slow_job)
        nezacaty = pool.submit(lambda: "nikdy")
        t0 = time.time()
        out = abort.gather(pool, [fast, slow, nezacaty], abort.never,
                            on_done=lambda f: f is fast, poll=0.05)
        self.assertLess(time.time() - t0, 1.0, "nesmí čekat na pomalou úlohu")
        self.assertEqual(out, [fast, slow, nezacaty])
        self.assertTrue(fast.done())
        self.assertTrue(nezacaty.cancelled())
        release.set()
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

    def test_hlavicky_cekaji_nejvys_strop_a_pomale_doctou_na_pozadi(self):
        """Pár pomalých souborů z HellSpy (2–3,7 s) drželo výběr streamu (Office 2026-09-18).
        Po `PROBE_DEADLINE` se nečeká; pomalý soubor doběhne a zapíše se do cache."""
        import nokturno_core.engine as engine_mod
        engine = Engine({"audio_probe": "24"}, self.dir)
        hotovo = threading.Event()

        def media(url):
            if url == "hs:pomaly":
                time.sleep(0.6)
                hotovo.set()
            return {"audio": [{"lang": "CZ", "channels": "5.1", "codec": "ac3"}], "height": 1080}
        engine._media_from_file = media
        engine.last_timings = {}
        streams = [{"url": "hs:pomaly", "label": "pomalý", "detail": ""},
                   {"url": "ws:rychly", "label": "rychlý", "detail": ""}]
        with mock.patch.object(engine_mod, "PROBE_DEADLINE", 0.2):
            t0 = time.monotonic()
            out = engine._fill_audio(streams)
            self.assertLess(time.monotonic() - t0, 0.5, "na pomalý soubor se nečeká")
        self.assertTrue(out[1].get("_tracks"), "rychlý soubor má ověřený zvuk")
        self.assertFalse(out[0].get("_tracks"), "pomalý zatím ne")
        self.assertEqual(engine.last_timings["hlaviček nedočteno"], 1)
        self.assertTrue(hotovo.wait(2), "pomalý soubor doběhne na pozadí")

    def test_na_pozadi_se_prectou_i_sloucene_a_nad_limit(self):
        """Přání uživatele 2026-09-18: nad rámec limitu přečíst na pozadí všechny dostupné
        streamy i sloučené verze — do cache pro další otevření a „Zobrazit všechny“."""
        engine = Engine({"audio_probe": "1", "probe_background": True}, self.dir)
        cteno, vse = [], threading.Event()

        def media(url):
            cteno.append(url)
            if len(cteno) == 4:
                vse.set()
            return {}
        engine._media_from_file = media
        engine.last_timings = {}
        streams = [{"url": "hs:1", "label": "a", "detail": ""}, {"url": "hs:2", "label": "b", "detail": ""},
                   {"url": "st:3", "label": "c", "detail": ""}]   # Sledujteto hlavičky nečte
        engine._fill_audio(streams, background=[{"url": "ws:4", "label": "d"}, {"url": "ws:5", "label": "e"}])
        self.assertTrue(vse.wait(2))
        self.assertEqual(sorted(cteno), ["hs:1", "hs:2", "ws:4", "ws:5"])
        self.assertEqual(engine.last_timings["hlavičky na pozadí"], 3)

    def test_bez_volby_nic_na_pozadi(self):
        engine = Engine({"audio_probe": "1"}, self.dir)
        cteno = []
        engine._media_from_file = lambda url: (cteno.append(url), {})[1]
        engine._fill_audio([{"url": "hs:1", "label": "a", "detail": ""}, {"url": "hs:2", "label": "b", "detail": ""}],
                           background=[{"url": "ws:4", "label": "d"}])
        time.sleep(0.2)
        self.assertEqual(cteno, ["hs:1"])

    def test_gather_se_stropem_vrati_i_nehotove(self):
        pool = ThreadPoolExecutor(max_workers=2)
        futures = [pool.submit(time.sleep, 0.01), pool.submit(time.sleep, 0.5)]
        t0 = time.monotonic()
        out = abort.gather(pool, futures, lambda: False, deadline=0.2, poll=0.05)
        self.assertLess(time.monotonic() - t0, 0.4)
        self.assertEqual([f.done() for f in out], [True, False])
        self.assertIsNone(futures[1].result(timeout=2), "nic se nezrušilo, úloha doběhla")

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


class TestEnrichPool(unittest.TestCase):
    """Sdílený executor `enrich` nesmí nechat viset nečinná vlákna: Kodi na ně po doběhnutí
    pluginu čeká a zablokovaná v C je nezabije (Office 2026-09-16, widget „Nově přidané")."""

    def tearDown(self):
        from nokturno_core.lib import enrich
        enrich.shutdown_pool()

    def workers(self):
        return [t for t in threading.enumerate() if t.name.startswith("nokturno-enrich") and t.is_alive()]

    def test_po_shutdown_vlakna_skonci_a_pool_jde_znovu(self):
        from nokturno_core.lib import enrich
        enrich.shutdown_pool()
        with unittest.mock.patch.object(enrich, "_lookup", return_value={"description": "popis"}):
            metas = [{"name": f"Film {i}", "year": "2020", "imdb_id": f"tt{i}"} for i in range(3)]
            self.assertEqual(enrich.enrich(metas, deadline=5), 3)
        self.assertTrue(self.workers(), "executor vznikl až při dotazu")
        enrich.shutdown_pool()
        deadline = time.time() + 3
        while self.workers() and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.workers(), [], "nečinná vlákna po shutdown skončila")
        with unittest.mock.patch.object(enrich, "_lookup", return_value={"description": "popis"}):
            self.assertEqual(enrich.enrich([{"name": "Další", "year": "2021", "imdb_id": "tt9"}], deadline=5), 1)

    def test_cancel_zrusi_nezacate(self):
        from nokturno_core.lib import enrich
        enrich.shutdown_pool()
        release = threading.Event()

        def lookup(*a, **k):
            release.wait(2)
            return {}
        with unittest.mock.patch.object(enrich, "_lookup", lookup):
            metas = [{"name": f"Film {i}", "year": "2020", "imdb_id": f"tx{i}"} for i in range(20)]
            enrich.enrich(metas, deadline=0.1)
            with enrich._INFLIGHT_LOCK:
                futures = list(enrich._INFLIGHT.values())
            enrich.shutdown_pool(cancel=True)
            release.set()
        self.assertGreaterEqual(sum(1 for f in futures if f.cancelled()), 20 - enrich.WORKERS)
