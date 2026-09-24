"""Hlídání nových dílů a titulů bez streamu (`nokturno_core/lib/watch.py`), bez sítě."""
import pathlib
import sys
import tempfile
import time
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import sync, watch  # noqa: E402
from nokturno_core.lib.store import Store  # noqa: E402


def ep(s, e, released="2026-01-01"):
    return {"id": f"tt9:{s}:{e}", "season": s, "episode": e, "title": f"Díl {e}", "released": released}


class FakeEngine:
    """Díly seriálu a čím se dají pustit — počítá dotazy na streamy."""

    def __init__(self, episodes=(), available=(), search=()):
        self._episodes = list(episodes)
        self.available = set(available)
        self._search = list(search)
        self.calls = []

    def episodes(self, sid, season=None):
        return self._episodes

    def streams_or_torrents(self, ctype, item_id, alt=None, series_id=None):
        self.calls.append(item_id)
        return [{"label": "1080p", "kind": "stream"}] if item_id in self.available else []

    def search(self, ctype, query, limit=20):
        return self._search


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(self.dir.name)


class TestSerialy(Base):
    def test_prvni_kontrola_jen_zapamatuje(self):
        watch.watch_series(self.store, "tt9", {"title": "Seriál"})
        eng = FakeEngine([ep(1, 1), ep(1, 2)], {"tt9:1:2"})
        self.assertEqual(watch.check_series(eng, self.store), ["tt9"])
        item = watch.series(self.store)["tt9"]
        self.assertEqual(item["available"]["episode"], 2)
        self.assertNotIn("new", item)
        self.assertEqual(watch.pending_notices(self.store), [])

    def test_novy_dil_se_nahlasi_jednou(self):
        watch.watch_series(self.store, "tt9", {"title": "Seriál"})
        eng = FakeEngine([ep(1, 1), ep(1, 2)], {"tt9:1:1"})
        watch.check_series(eng, self.store)
        eng.available.add("tt9:1:2")
        watch.check_series(eng, self.store, force=True)
        self.assertEqual(watch.series(self.store)["tt9"]["new"]["episode"], 2)
        notices = watch.pending_notices(self.store)
        self.assertEqual([(n["kind"], n["title"], n["episode"]) for n in notices], [("episode", "Seriál", 2)])
        self.assertEqual(watch.pending_notices(self.store), [])
        self.assertEqual(watch.mark_seen(self.store, "tt9"), 0)

    def test_dil_bez_data_se_nehleda(self):
        watch.watch_series(self.store, "tt9")
        eng = FakeEngine([ep(1, 1), ep(1, 2, released="")], {"tt9:1:1"})
        watch.check_series(eng, self.store)
        self.assertNotIn("tt9:1:2", eng.calls)

    def test_kontrola_z_jineho_zarizeni_se_pocita(self):
        watch.watch_series(self.store, "tt9")
        eng = FakeEngine([ep(1, 1)], {"tt9:1:1"})
        watch.check_series(eng, self.store)
        eng.calls.clear()
        self.assertEqual(watch.check_series(eng, self.store), [])
        self.assertEqual(eng.calls, [])
        self.assertFalse(watch.anything_due(self.store))

    def test_rozpocet_dotazu(self):
        watch.watch_series(self.store, "tt9")
        eng = FakeEngine([ep(1, e) for e in range(1, 30)], {f"tt9:1:{e}" for e in range(1, 30)})
        with self.store.updating(watch.SERIES, {}) as data:
            data["tt9"]["available"] = {"season": 1, "episode": 1}
            data["tt9"]["checked"] = "x"
        watch.check_series(eng, self.store)
        self.assertEqual(len(eng.calls), watch.BUDGET)

    def test_zhasnuti_behem_kontroly_neprepise(self):
        """Nový díl zhasnutý odjinud mezitím, co kontrola běžela, se nevrátí."""
        watch.watch_series(self.store, "tt9")
        item = dict(watch.series(self.store)["tt9"], new={"id": "x"}, available={"season": 1, "episode": 1},
                    checked="x")
        result = {"latest": None, "found": None, "available": item["available"]}
        watch._save_series(self.store, "tt9", item, result, int(time.time()))
        self.assertNotIn("new", watch.series(self.store)["tt9"])


class TestHlidane(Base):
    def test_objeveni_streamu_a_narust(self):
        watch.want(self.store, "tt5", {"title": "Film", "type": "movie"})
        eng = FakeEngine()
        watch.check_wanted(eng, self.store)
        self.assertEqual(watch.results(self.store)["tt5"]["streams"], 0)
        eng.available.add("tt5")
        watch.check_wanted(eng, self.store, force=True)
        notices = watch.pending_notices(self.store)
        self.assertEqual([(n["kind"], n["streams"]) for n in notices], [("available", 1)])

    def test_dotaz_bez_shody_ceka(self):
        wid = watch.query_id("Duna 3")
        watch.want(self.store, wid, {"query": "Duna 3"})
        eng = FakeEngine(search=[{"id": "tt1", "title": "Vánoční prázdniny"}])
        watch.check_wanted(eng, self.store)
        self.assertTrue(watch.results(self.store)[wid]["pending"])

    def test_odebrani_smaze_vysledek_i_priznak(self):
        watch.want(self.store, "tt5")
        watch.check_wanted(FakeEngine(available={"tt5"}), self.store)
        self.assertTrue(watch.toggle_flag(self.store, "tt5"))
        watch.unwant(self.store, "tt5")
        self.assertEqual((watch.wanted(self.store), watch.results(self.store), watch.flags(self.store)), ({}, {}, {}))

    def test_trakt_se_kontroluje_ale_neprenasi(self):
        watch.check_wanted(FakeEngine(available={"tt7"}), self.store, extra=[{"id": "tt7", "type": "movie"}])
        self.assertIn("tt7", watch.results(self.store))
        self.assertEqual(watch.collect(self.store, 0, sync._seen), {})


class TestSynchronizace(Base):
    def other(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Store(d.name)

    def test_seznam_priznak_a_odebrani_projdou(self):
        watch.watch_series(self.store, "tt9", {"title": "Seriál"})
        watch.want(self.store, "tt5", {"title": "Film"})
        watch.toggle_flag(self.store, "tt5")
        b = self.other()
        self.assertEqual(sync.apply_changes(b, sync.collect_changes(self.store, 0)), 2)
        self.assertIn("tt9", watch.series(b))
        self.assertTrue(watch.is_flagged(b, "tt5"))
        time.sleep(1.05)
        watch.unwant(self.store, "tt5")
        sync.apply_changes(b, sync.collect_changes(self.store, 0))
        self.assertEqual(watch.wanted(b), {})
        self.assertEqual(watch.flags(b), {})

    def test_novejsi_vyhrava(self):
        watch.watch_series(self.store, "tt9", {"title": "A"})
        b = self.other()
        time.sleep(1.05)
        watch.watch_series(b, "tt9", {"title": "B"})
        sync.apply_changes(b, sync.collect_changes(self.store, 0))
        self.assertEqual(watch.series(b)["tt9"]["title"], "B")

    def test_nalez_odjinud_se_oznami(self):
        watch.watch_series(self.store, "tt9", {"title": "Seriál"})
        with self.store.updating(watch.SERIES, {}) as data:
            data["tt9"]["new"] = {"id": "tt9:1:2", "season": 1, "episode": 2, "ts": int(time.time())}
        watch._touch(self.store, "s:tt9", True)
        b = self.other()
        sync.apply_changes(b, sync.collect_changes(self.store, 0))
        self.assertEqual(len(watch.pending_notices(b)), 1)

    def test_stary_nalez_se_neoznami(self):
        watch.watch_series(self.store, "tt9")
        with self.store.updating(watch.SERIES, {}) as data:
            data["tt9"]["new"] = {"id": "tt9:1:2", "season": 1, "episode": 2}   # bez času = z doby před jádrem
        self.assertEqual(watch.pending_notices(self.store), [])

    def test_okruh_jde_vypnout(self):
        watch.want(self.store, "tt5")
        changes = sync.collect_changes(self.store, 0)
        self.assertNotIn(watch.SECTION, sync.filter_circles(changes, ("watched",)))
        self.assertIn(watch.SECTION, sync.filter_circles(changes, sync.DEFAULT_CIRCLES))


if __name__ == "__main__":
    unittest.main()


class TestVypadek(Base):
    def test_vypadek_site_neprepise_nalez(self):
        watch.want(self.store, "tt5")
        watch.check_wanted(FakeEngine(), self.store)
        watch.check_wanted(FakeEngine(available={"tt5"}), self.store, force=True)
        watch.pending_notices(self.store)
        watch.check_wanted(FakeEngine(), self.store, force=True)        # bez sítě
        self.assertEqual(watch.results(self.store)["tt5"]["streams"], 1)
        watch.check_wanted(FakeEngine(available={"tt5"}), self.store, force=True)
        self.assertEqual(watch.pending_notices(self.store), [])

    def test_oznameni_bez_zmeny_nezapisuje(self):
        watch.watch_series(self.store, "tt9")
        watch.pending_notices(self.store)
        with unittest.mock.patch.object(self.store, "save") as save:
            watch.pending_notices(self.store)
        save.assert_not_called()
