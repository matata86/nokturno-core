"""Okruhy u synchronizace přes Home Assistant (`nokturno_core/lib/sync.py`).

Přepínače „co se synchronizuje" byly původně jen u slepého relaye; tyhle testy
hlídají, že u HA platí stejně — odchozí i příchozí stranu, a že zapnutí okruhu
dorovná i to, co přišlo, dokud byl vypnutý.
"""
import io
import json
import pathlib
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
        self.store.save("watched", {"tt1": {"resume": 0, "total": 0, "ts": ted}})
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
