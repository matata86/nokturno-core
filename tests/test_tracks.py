"""Výběr zvukové stopy a titulků podle preferovaného jazyka a rozpoznání jazyka titulků."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nokturno_core.lib import tracks  # noqa: E402


def a(index, language="", name="", channels=2, **kw):
    return dict(index=index, language=language, name=name, channels=channels, **kw)


class TestJazykStopy(unittest.TestCase):
    def test_kody_a_nazvy(self):
        self.assertEqual(tracks.track_lang({"language": "cze"}), "CZ")
        self.assertEqual(tracks.track_lang({"language": "ces"}), "CZ")
        self.assertEqual(tracks.track_lang({"language": "slk"}), "SK")
        self.assertEqual(tracks.track_lang({"language": "eng"}), "EN")
        self.assertEqual(tracks.track_lang({"language": "und", "name": "CZ Dabing 5.1"}), "CZ")
        self.assertEqual(tracks.track_lang({"language": "", "name": "Čeština"}), "CZ")
        self.assertEqual(tracks.track_lang({"language": "", "name": "Matrix CZtit"}), "CZ")
        self.assertEqual(tracks.track_lang({"language": "ger", "name": "CZ"}), "",
                         "známý cizí jazyk nepřebije název")
        self.assertEqual(tracks.track_lang({"language": "", "name": "Stereo"}), "")


class TestZvuk(unittest.TestCase):
    def test_prepne_na_cestinu_s_nejvic_kanaly(self):
        stopy = [a(0, "eng", channels=6), a(1, "cze", "Komentář režiséra", channels=6),
                 a(2, "cze", channels=2), a(3, "cze", channels=6)]
        self.assertEqual(tracks.pick_audio(stopy, {"index": 0}, "CZ"), (3, True))

    def test_uz_hraje_nic_nemeni(self):
        stopy = [a(0, "cze", channels=2), a(1, "cze", channels=6)]
        self.assertEqual(tracks.pick_audio(stopy, {"index": 0}, "CZ"), (None, True))

    def test_cestina_chybi(self):
        self.assertEqual(tracks.pick_audio([a(0, "eng")], {"index": 0}, "CZ"), (None, False))

    def test_neoznacene_stopy_rozhodne_zdroj(self):
        stopy = [a(0, "und")]
        self.assertEqual(tracks.pick_audio(stopy, {"index": 0}, "CZ"), (None, None))
        self.assertEqual(tracks.pick_audio(stopy, {"index": 0}, "CZ", ["CZ"]), (None, True))
        self.assertEqual(tracks.pick_audio(stopy, {"index": 0}, "CZ", ["EN"]), (None, False))

    def test_bez_preference(self):
        self.assertEqual(tracks.pick_audio([a(0, "eng")], {"index": 0}, ""), (None, None))


class TestTitulky(unittest.TestCase):
    SUBS = [
        {"index": 0, "language": "eng"},
        {"index": 1, "language": "slo"},
        {"index": 2, "language": "cze", "isimpaired": True},
        {"index": 3, "language": "cze"},
        {"index": 4, "language": "cze", "isforced": True},
    ]

    def test_bez_ceskeho_zvuku_plne_ceske(self):
        self.assertEqual(tracks.pick_subtitle(self.SUBS, "CZ", False), ("on", 3))

    def test_slovenske_jako_nahrada(self):
        self.assertEqual(tracks.pick_subtitle(self.SUBS[:2], "CZ", False), ("on", 1))
        self.assertEqual(tracks.pick_subtitle(self.SUBS[:1], "CZ", False), ("keep", None))

    def test_dabing_vypne_titulky_i_vynucene(self):
        self.assertEqual(tracks.pick_subtitle(self.SUBS, "CZ", True), ("off", None))
        self.assertEqual(tracks.pick_subtitle(self.SUBS[:4], "CZ", True), ("off", None))

    def test_vynucene_jen_podle_nazvu(self):
        """Office 2026-09-16: „CZE forced“ bez příznaku `isforced`."""
        subs = [{"index": 0, "language": "cze", "name": "CZE"},
                {"index": 1, "language": "cze", "name": "CZE forced", "isforced": False, "isdefault": True}]
        self.assertEqual(tracks.pick_subtitle(subs, "CZ", False), ("on", 0), "vynucené nejsou plné titulky")
        self.assertEqual(tracks.pick_subtitle(subs, "CZ", True), ("off", None))

    def test_nevime_co_hraje(self):
        self.assertEqual(tracks.pick_subtitle(self.SUBS, "CZ", None), ("keep", None))

    def test_rezimy(self):
        self.assertEqual(tracks.pick_subtitle(self.SUBS, "CZ", True, tracks.SUBS_ALWAYS), ("on", 3))
        self.assertEqual(tracks.pick_subtitle(self.SUBS, "CZ", False, tracks.SUBS_KEEP), ("keep", None))


CZ_TEXT = """1
00:00:01,000 --> 00:00:03,000
Řekni mi, proč jsi tady a co tu děláš.

2
00:00:04,000 --> 00:00:06,000
<i>Nevím, jestli můžu věřit tomu, že přijdeš.</i>
""" * 8
SK_TEXT = """1
00:00:01,000 --> 00:00:03,000
Povedz mi, prečo si tu a čo tu robíš.

2
00:00:04,000 --> 00:00:06,000
Neviem, či môžem veriť tomu, že prídeš, ľudia hovoria všeličo.
""" * 8
EN_TEXT = """1
00:00:01,000 --> 00:00:03,000
Tell me what you are doing here and why.

2
00:00:04,000 --> 00:00:06,000
I don't know if I can trust that you will come to the party.
""" * 8
CZ_ASCII = """1
00:00:01,000 --> 00:00:03,000
Rekni mi, proc jsi tady. Jsem rad, ze jsi prisel.

2
00:00:04,000 --> 00:00:06,000
Neni to jednoduche, ale taky nevim, co dal.
""" * 8


class TestStazeneTitulky(unittest.TestCase):
    def test_jazyk_podle_textu(self):
        self.assertEqual(tracks.subtitle_lang(CZ_TEXT), "CZ")
        self.assertEqual(tracks.subtitle_lang(SK_TEXT), "SK")
        self.assertEqual(tracks.subtitle_lang(EN_TEXT), "EN")
        self.assertEqual(tracks.subtitle_lang(CZ_ASCII), "CZ")
        self.assertEqual(tracks.subtitle_lang("1\n00:00:01,000 --> 00:00:02,000\nAhoj\n"), "",
                         "z pár slov se nehádá")
        polstina = "Nie wiem, czy mogę ci zaufać. Dlaczego tu jesteś i co robisz? Przyjdź jutro. " * 10
        self.assertEqual(tracks.subtitle_lang(polstina), "")

    def test_kodovani(self):
        self.assertEqual(tracks.decode_subtitle(CZ_TEXT.encode("cp1250")), CZ_TEXT)
        self.assertEqual(tracks.decode_subtitle(CZ_TEXT.encode("utf-8")), CZ_TEXT)
        self.assertEqual(tracks.decode_subtitle(b"\xef\xbb\xbf" + CZ_TEXT.encode("utf-8")), CZ_TEXT)
        self.assertEqual(tracks.decode_subtitle(CZ_TEXT.encode("utf-16")), CZ_TEXT)

    def test_format(self):
        self.assertEqual(tracks.subtitle_format("WEBVTT\n\n00:01.000 --> 00:02.000\nx"), "vtt")
        self.assertEqual(tracks.subtitle_format("[Script Info]\nTitle: x"), "ass")
        self.assertEqual(tracks.subtitle_format(CZ_TEXT), "srt")


if __name__ == "__main__":
    unittest.main()
