"""Okruhy u synchronizace přes Home Assistant (`nokturno_core/lib/sync.py`).

Přepínače „co se synchronizuje" byly původně jen u slepého relaye; tyhle testy
hlídají, že u HA platí stejně — odchozí i příchozí stranu, a že zapnutí okruhu
dorovná i to, co přišlo, dokud byl vypnutý.
"""
import io
import json
import pathlib
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import sync  # noqa: E402
from nokturno_core.lib.store import Store  # noqa: E402


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class TestOkruhyHA(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Store(self.dir.name)
        ted = int(time.time())
        # rozkoukaný: snímek k němu jde do výměny (dokoukaný si příjemce dohledá sám)
        self.store.save("watched", {"tt1": {"resume": 100, "total": 1000, "ts": ted}})
        self.store.save("favlog", {"tt2": {"on": True, "ts": ted}})
        self.store.save("histlog", {"matrix": {"ts": ted}})
        self.store.save("items", {"tt1": {"title": "Film"}, "tt2": {"title": "Jiný"}})
        self.odeslane = []
        self.odpoved = {"now": ted, "changes": {}}

    def _run(self, circles, odpoved=None):
        def falesny_urlopen(req, timeout=None):
            self.odeslane.append(json.loads(req.data.decode("utf-8")))
            telo = json.dumps(odpoved if odpoved is not None else self.odpoved)
            return FakeResponse(telo.encode("utf-8"))

        with mock.patch.object(sync.urllib.request, "urlopen", falesny_urlopen):
            return sync.sync_once(self.store, "http://ha", "klic", "Obývák", circles=circles)

    def test_vypnuty_okruh_se_neposila(self):
        self._run(("watched",))
        zmeny = self.odeslane[-1]["changes"]
        self.assertIn("tt1", zmeny["watched"])
        self.assertNotIn("favlog", zmeny)
        self.assertNotIn("histlog", zmeny)
        self.assertIn("tt1", zmeny["items"])   # snímky jdou vždy s tím, co zbyde

    def test_bez_okruhu_se_posila_vse(self):
        self._run(None)
        zmeny = self.odeslane[-1]["changes"]
        self.assertTrue(zmeny["favlog"] and zmeny["histlog"] and zmeny["watched"])

    def test_vypnuty_okruh_se_neprijima(self):
        ted = int(time.time())
        self.odpoved = {"now": ted, "changes": {
            "watched": {"tt9": {"resume": 5, "total": 10, "ts": ted}},
            "favlog": {"tt8": {"on": True, "ts": ted}},
        }}
        self._run(("watched",))
        self.assertIn("tt9", self.store.reload("watched", {}))
        self.assertNotIn("tt8", self.store.reload("favlog", {}))

    def test_zapnuty_okruh_si_vyzada_vse_znovu(self):
        self._run(("watched",))
        self.assertGreater(self.store.reload(sync.STATE, {}).get("since") or 0, 0)
        self._run(("watched", "favourites"))
        self.assertEqual(self.odeslane[-1]["since"], 0)      # sada se změnila → od začátku
        self._run(("watched", "favourites"))
        self.assertGreater(self.odeslane[-1]["since"], 0)    # beze změny se pokračuje


if __name__ == "__main__":
    unittest.main()


class TestResetSince(unittest.TestCase):
    """Most mezi středisky: po příjmu z relaye musí jít celý stav i do HA."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.store = Store(self.tmp)

    def test_vynuluje_since_a_zbytek_necha(self):
        self.store.save(sync.STATE, {"since": 12345, "last_ok": 9, "device": "x"})
        sync.reset_since(self.store)
        stav = self.store.reload(sync.STATE, {})
        self.assertEqual(stav["since"], 0)
        self.assertEqual(stav["last_ok"], 9)
        self.assertEqual(stav["device"], "x")

    def test_bez_since_nic_nezapisuje(self):
        self.store.save(sync.STATE, {"since": 0, "last_ok": 5})
        sync.reset_since(self.store)
        self.assertEqual(self.store.reload(sync.STATE, {})["last_ok"], 5)


class TestSnimkyVeVymene(unittest.TestCase):
    """Snímky titulů jsou jediná část stavu, která roste bez omezení.

    Bez stropu má profil s pár sty zhlédnutými tituly blob přes `syncbox.MAX_BLOB`
    (měřeno: 500 titulů = 177 kB proti limitu 128 kB) a synchronizace přestane
    fungovat úplně. Proto jdou do výměny jen snímky, bez kterých se položka
    nevykreslí — Můj seznam a rozkoukané.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.store = Store(self.tmp)

    def test_dokoukany_titul_snimek_neveze(self):
        ted = int(time.time())
        self.store.save("watched", {"hotovo": {"resume": 0, "total": 100, "ts": ted},
                                    "rozkoukane": {"resume": 50, "total": 100, "ts": ted}})
        self.store.save("favlog", {"seznam": {"on": True, "ts": ted},
                                   "odebrane": {"on": False, "ts": ted}})
        self.store.save("items", {k: {"title": k} for k in
                                  ("hotovo", "rozkoukane", "seznam", "odebrane")})
        items = sync.collect_changes(self.store, 0)["items"]
        self.assertEqual(set(items), {"rozkoukane", "seznam"})

    def test_strop_bere_nejcerstvejsi(self):
        ted = int(time.time())
        pocet = sync.SNAPSHOT_MAX + 50
        self.store.save("watched", {"tt%d" % i: {"resume": 10, "total": 100, "ts": ted - i}
                                    for i in range(pocet)})
        self.store.save("items", {"tt%d" % i: {"title": str(i)} for i in range(pocet)})
        items = sync.collect_changes(self.store, 0)["items"]
        self.assertEqual(len(items), sync.SNAPSHOT_MAX)
        self.assertIn("tt0", items)                      # nejčerstvější
        self.assertNotIn("tt%d" % (pocet - 1), items)    # nejstarší


class TestSlevaniZhlednuti(unittest.TestCase):
    """„Tohle už jsem viděl" se slitím dvou zařízení ztratit nesmí."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.store = Store(self.tmp)

    def test_novejsi_rozkoukanost_neprebije_dokoukano(self):
        ted = int(time.time())
        self.store.save("watched", {"tt1": {"resume": 0, "total": 100,
                                            "playcount": 1, "ts": ted - 100}})
        sync.apply_changes(self.store, {"watched": {
            "tt1": {"resume": 30, "total": 100, "ts": ted}}})
        rec = self.store.reload("watched", {})["tt1"]
        self.assertEqual(rec["playcount"], 1)       # zhlédnutí zůstalo
        self.assertEqual(rec["resume"], 30)         # pozice je z novějšího záznamu

    def test_vyssi_pocet_zhlednuti_z_druhe_strany_vyhraje(self):
        ted = int(time.time())
        self.store.save("watched", {"tt1": {"resume": 0, "playcount": 1, "ts": ted - 100}})
        sync.apply_changes(self.store, {"watched": {"tt1": {"resume": 0, "playcount": 3, "ts": ted}}})
        self.assertEqual(self.store.reload("watched", {})["tt1"]["playcount"], 3)
