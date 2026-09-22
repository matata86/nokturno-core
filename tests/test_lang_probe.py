"""Jazykový katalog: předčasný konec kola a Přehraj.to bez účtu ven (2026-09-22).

Kolo zdrojů v `raw_streams(stop_when=...)` smí skončit hned, jak některý zdroj
nabídne dost (dabing má přednost) — a hromadná klasifikace (`Engine.classify_langs()`)
vynechává Přehraj.to bez účtu úplně, protože do ní nepřidá nic navíc. Testy jsou
bez sítě: zdroje se podstrkují přes `mock.patch.object`.

    python3 -m unittest tests.test_lang_probe -v
"""
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core import Engine    # noqa: E402

META_VIDEO = ({"name": "Matrix", "year": 1999}, None)


class TestPrehrajtoBezUctuVen(unittest.TestCase):
    """`ostatni()` v `raw_streams()`: Přehraj.to jede v hromadné klasifikaci
    (`probe_audio=False`) jen s Premium účtem, v normálním dialogu streamů
    (`probe_audio=True`) vždycky — beze změny chování."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.engine = Engine({}, self.tmp)

    def test_probe_audio_false_bez_uctu_se_pt_nevola(self):
        with mock.patch.object(Engine, "_prehrajto_streams") as pt:
            self.engine.raw_streams("movie", "tt0133093", strict=True, probe_audio=False,
                                     meta_video=META_VIDEO)
        pt.assert_not_called()

    def test_probe_audio_false_s_uctem_se_pt_vola(self):
        fake_pt = mock.Mock()
        fake_pt._account = True
        with mock.patch.object(Engine, "pt", new_callable=mock.PropertyMock, return_value=fake_pt), \
             mock.patch.object(Engine, "_prehrajto_streams", return_value=[]) as pt:
            self.engine.raw_streams("movie", "tt0133093", strict=True, probe_audio=False,
                                     meta_video=META_VIDEO)
        pt.assert_called()

    def test_probe_audio_true_bez_uctu_se_pt_vola(self):
        """Normální dialog streamů se nemění — Přehraj.to zůstává i bez účtu."""
        with mock.patch.object(Engine, "_prehrajto_streams", return_value=[]) as pt:
            self.engine.raw_streams("movie", "tt0133093", strict=True, probe_audio=True,
                                     meta_video=META_VIDEO)
        pt.assert_called()


class TestStopWhen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.engine = Engine({}, self.tmp)

    def test_stop_when_neceka_na_pomaly_zdroj(self):
        """Rychlý zdroj nahlásí CZ dabing, `stop_when` řekne dost — pomalý zdroj
        (5 s) se nestihne a `failures` je prázdné (nedoběhl ze záměru, ne z chyby)."""
        failures = []

        def rychly(*a, **k):
            # `quality_rank` už v datech = `parse_stream()` je bere jako hotové
            # a langs/subs nepřepíše odhadem z (chybějícího) názvu souboru; zbytek
            # polí čte řazení (`arrange()`) i doplnění velikosti/kvality níž
            return [{"url": "hs:1", "langs": ["CZ"], "subs": [], "quality_rank": 0,
                     "size_gb": 1.0, "bitrate": 0.0, "duration": 0, "channels": {}}]

        def pomaly(*a, **k):
            time.sleep(5)
            return []

        # `_with_local_title` chodí na Wikidatu i mimo `SOURCE_DEADLINE` (viz
        # tests/test_smoke.py a poznámka v paměti „časové testy jádra
        # bez sítě") — bez fake by test závisel na reálné síti a byl pomalý.
        with mock.patch.object(Engine, "_hellspy_streams", side_effect=rychly), \
             mock.patch.object(Engine, "_webshare_streams", side_effect=pomaly), \
             mock.patch.object(Engine, "_prehrajto_streams", return_value=[]), \
             mock.patch.object(Engine, "_with_local_title", side_effect=lambda ctype, base_id, meta: meta):
            t0 = time.time()
            found = self.engine.raw_streams(
                "movie", "tt0133093", strict=True, probe_audio=False, failures=failures,
                meta_video=META_VIDEO,
                stop_when=lambda st: any({"CZ"} & set(s.get("langs") or []) for s in st or []))
        self.assertLess(time.time() - t0, 2.0, "nesmí čekat na pomalý zdroj")
        self.assertEqual(failures, [])
        self.assertTrue(any(s["url"] == "hs:1" for s in found))


class TestClassifyLangs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.engine = Engine({}, self.tmp)

    def test_dabing(self):
        with mock.patch.object(Engine, "raw_streams", return_value=[{"langs": ["CZ"], "subs": []}]):
            self.assertEqual(self.engine.classify_langs("movie", "tt1")["k"], "dub")

    def test_jen_titulky(self):
        with mock.patch.object(Engine, "raw_streams", return_value=[{"langs": [], "subs": ["SK"]}]):
            self.assertEqual(self.engine.classify_langs("movie", "tt2")["k"], "subs")

    def test_nic(self):
        with mock.patch.object(Engine, "raw_streams", return_value=[{"langs": ["EN"], "subs": []}]):
            self.assertEqual(self.engine.classify_langs("movie", "tt3")["k"], "")

    def test_dabing_ma_prednost(self):
        streams = [{"langs": [], "subs": ["CZ"]}, {"langs": ["CZ"], "subs": []}]
        with mock.patch.object(Engine, "raw_streams", return_value=streams):
            self.assertEqual(self.engine.classify_langs("movie", "tt4")["k"], "dub")

    def test_cache_drzi(self):
        with mock.patch.object(Engine, "raw_streams", return_value=[{"langs": ["CZ"], "subs": []}]) as rs:
            self.engine.classify_langs("movie", "tt5")
            self.engine.classify_langs("movie", "tt5")
        self.assertEqual(rs.call_count, 1)

    def test_prazdny_vysledek_se_necachuje(self):
        with mock.patch.object(Engine, "raw_streams", return_value=[]) as rs:
            self.engine.classify_langs("movie", "tt6")
            self.engine.classify_langs("movie", "tt6")
        self.assertEqual(rs.call_count, 2)


if __name__ == "__main__":
    unittest.main()
