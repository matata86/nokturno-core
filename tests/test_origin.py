import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nokturno_core.lib.streams import assume_origin_language, origin_languages


class TestOriginLanguage(unittest.TestCase):
    def test_zeme_puvodu(self):
        self.assertEqual(origin_languages("Czech Republic"), ["CZ"])
        self.assertEqual(origin_languages("Czechoslovakia"), ["CZ"])
        self.assertEqual(origin_languages("Slovakia"), ["SK"])
        self.assertEqual(origin_languages("Česko, Slovensko"), ["CZ", "SK"])
        self.assertEqual(origin_languages("United States of America"), [])
        self.assertEqual(origin_languages(None), [])

    def test_en_u_ceske_tvorby_je_jen_odhad(self):
        s = [{"langs": ["EN"], "label": "Hospoda.S01E02.1080p.mkv"}]
        assume_origin_language(s, "Czech Republic")
        self.assertEqual(s[0]["langs"], ["CZ"])
        self.assertTrue(s[0]["_langs_from_name"], "odhad, ne ověřený údaj")

    def test_anglicky_nazev_souboru_zustane(self):
        s = [{"langs": ["EN"], "label": "Hospoda.S01E02.ENG.1080p.mkv"}]
        assume_origin_language(s, "Czech Republic")
        self.assertEqual(s[0]["langs"], ["EN"])

    def test_zahranicni_titul_a_vicejazycny_zvuk_beze_zmeny(self):
        s = [{"langs": ["EN"], "label": "Film.mkv"}, {"langs": ["CZ", "EN"], "label": "Film.CZ.mkv"}]
        assume_origin_language(s, "United States of America")
        self.assertEqual([x["langs"] for x in s], [["EN"], ["CZ", "EN"]])
        assume_origin_language(s, "Czech Republic")
        self.assertEqual(s[1]["langs"], ["CZ", "EN"])


if __name__ == "__main__":
    unittest.main()
