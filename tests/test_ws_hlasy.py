"""Soubory z WebShare s převahou záporných hlasů se z běžného výpisu vynechají."""
import tempfile
import unittest

from nokturno_core.engine import WS_HIDE_SCORE, Engine


def _file(ident, pos, neg):
    return {"ident": ident, "name": "Matrix.1999.1080p.CZ.mkv", "size": 2 * 1024 ** 3, "size_h": "2 GB",
            "positive": pos, "negative": neg}


class _Ws:
    def __init__(self, files):
        self.files = files

    def search(self, query, limit=25):
        return list(self.files), len(self.files)


class TestWsHlasy(unittest.TestCase):
    def _engine(self, tmp, files):
        engine = Engine({}, tmp)
        engine._ws, engine._ws_ready = _Ws(files), True
        return engine

    def _idents(self, engine, strict):
        meta = {"name": "Matrix", "_title": "Matrix", "year": 1999}
        engine.original_titles = lambda *a, **k: []
        return {s["url"] for s in engine._webshare_streams(meta, strict=strict)}

    def test_prah(self):
        self.assertEqual(WS_HIDE_SCORE, -2)

    def test_skore(self):
        self.assertEqual(Engine._ws_score({"positive": 3, "negative": 5}), -2)
        self.assertEqual(Engine._ws_score({}), 0)

    def test_skryje_jen_skore_pod_prahem(self):
        files = [_file("a", 3, 5),    # −2 → skrytý
                 _file("b", 0, 2),    # −2 → skrytý
                 _file("c", 0, 1),    # −1 → vidět
                 _file("d", 5, 6),    # −1 → vidět
                 _file("e", 10, 4),   # +6 → vidět
                 _file("f", 0, 0)]    # bez hlasů → vidět
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp, files)
            self.assertEqual(self._idents(engine, True), {"ws:c", "ws:d", "ws:e", "ws:f"})

    def test_uvolneny_fulltext_ukaze_vsechno(self):
        files = [_file("a", 3, 5), _file("b", 0, 2), _file("c", 0, 0)]
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp, files)
            self.assertEqual(self._idents(engine, False), {"ws:a", "ws:b", "ws:c"})


if __name__ == "__main__":
    unittest.main()
