"""Titulky z OpenSubtitles (`nokturno_core/lib/opensubtitles_api.py`) — bez sítě.

Hlídá to, co se u tohohle zdroje dá tiše rozbít:
* otisk souboru (algoritmus OpenSubtitles) — jiné číslo znamená, že se nikdy nic netrefí,
* pořadí parametrů v dotazu — neseřazené abecedně dostanou od serveru 301,
* přichycení titulků k jinému dílu — chyba, kterou u WebShare řešila 5.2.34,
* to, že se kvůli titulkům nikdy nestáhne víc souborů, než dovolí denní kvóta.
"""
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib.opensubtitles_api import (      # noqa: E402
    BLOK, KvotaVycerpana, OpenSubtitlesApi, OpenSubtitlesError, otisk)
from nokturno_core.lib.store import Store              # noqa: E402

KLIC = "a" * 32


class Resp:
    def __init__(self, data):
        self.body = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


def titulek(file_id, lang="cs", season=None, episode=None, hash_match=False, downloads=0,
            machine=False, release="Neco.1080p"):
    detail = {}
    if season is not None:
        detail = {"season_number": season, "episode_number": episode}
    return {"attributes": {
        "language": lang, "release": release, "download_count": downloads,
        "moviehash_match": hash_match, "machine_translated": machine,
        "feature_details": detail, "files": [{"file_id": file_id, "file_name": release}],
    }}


class TestOtisk(unittest.TestCase):
    """Algoritmus je ověřený proti oficiálnímu testovacímu vektoru OpenSubtitles
    (`breakdance.avi`, 12 909 756 B → `8e245d9679d31e12`) — ověřeno 2026-09-20 nad
    staženým souborem, který v repozitáři nedrží. Testy níž hlídají to, co se dá
    rozbít bez něj: šířku, endianitu, přetečení a odmítnutí neúplného výřezu."""

    def test_nulovy_soubor(self):
        """Dva bloky nul o velikosti 131072 dávají právě velikost — kontrola šířky i endianity."""
        self.assertEqual(otisk(bytes(BLOK), bytes(BLOK), 2 * BLOK), "0000000000020000")

    def test_little_endian(self):
        """Bajty se sčítají po osmi jako little-endian: 0x01 na začátku bloku přidá 1."""
        zacatek = b"\x01" + bytes(BLOK - 1)
        self.assertEqual(otisk(zacatek, bytes(BLOK), 2 * BLOK), "0000000000020001")

    def test_preteceni_v_64_bitech(self):
        """Součet se ořezává na 64 bitů, jinak by Python počítal dál a vyšlo delší číslo."""
        plny = b"\xff" * 8 + bytes(BLOK - 8)
        znak = otisk(plny, bytes(BLOK), 2 * BLOK)
        self.assertEqual(len(znak), 16)
        self.assertEqual(znak, "000000000001ffff")

    def test_kratky_soubor_nema_otisk(self):
        """Z kratšího souboru by vyšlo jiné číslo než tomu, kdo ho má na disku — radši nic."""
        self.assertEqual(otisk(b"x" * 10, b"y" * 10, 20), "")

    def test_neuplny_vyrez_nema_otisk(self):
        """Server, který pošle míň, než jsme chtěli (Range neumí) — taky nic."""
        self.assertEqual(otisk(bytes(BLOK - 1), bytes(BLOK), 2 * BLOK), "")


class TestDotaz(unittest.TestCase):
    def _api(self, odpoved, **kw):
        api = OpenSubtitlesApi(KLIC, **kw)
        api._pauza = lambda: None      # test nečeká na odstup mezi dotazy
        self.volane = []

        def fake(req, timeout=None):
            self.volane.append(req)
            return Resp(odpoved)

        self.patch = mock.patch.object(urllib.request, "urlopen", fake)
        return api

    def test_parametry_serazene_abecedne(self):
        """Server odpoví 301 na dotaz, který nemá parametry abecedně — kanonizuje si cache."""
        api = self._api({"data": [titulek(1)]})
        with self.patch:
            api.hledej("tt14688458", ("CZ",), season=1, episode=2)
        dotaz = urllib.parse.urlsplit(self.volane[0].full_url).query
        klice = [d.split("=")[0] for d in dotaz.split("&")]
        self.assertEqual(klice, sorted(klice), dotaz)

    def test_klic_v_hlavicce_ne_v_adrese(self):
        """Klíč patří do hlavičky `Api-Key`; v adrese by skončil v logu proxy."""
        api = self._api({"data": []})
        with self.patch:
            api.hledej("tt0133093", ("CZ",))
        self.assertEqual(self.volane[0].get_header("Api-key"), KLIC)
        self.assertNotIn(KLIC, self.volane[0].full_url)

    def test_seriál_jde_na_rodicovske_id(self):
        """U dílu se ptáme na `parent_imdb_id` + číslo sezóny a dílu, ne na `imdb_id`."""
        api = self._api({"data": []})
        with self.patch:
            api.hledej("tt14688458", ("CZ",), season=1, episode=2)
        dotaz = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.volane[0].full_url).query))
        self.assertEqual(dotaz.get("parent_imdb_id"), "14688458")
        self.assertEqual((dotaz.get("season_number"), dotaz.get("episode_number")), ("1", "2"))
        self.assertNotIn("imdb_id", dotaz)

    def test_jiny_dil_se_zahodi(self):
        """Chyba z 5.2.34 u WebShare: titulky k jinému dílu se nesmí přichytit.
        Tady to hlídá `feature_details` z odpovědi, ne regulár nad názvem souboru."""
        api = self._api({"data": [titulek(1, season=1, episode=4), titulek(2, season=2, episode=1)]})
        with self.patch:
            nalez = api.hledej("tt9999999", ("CZ",), season=2, episode=1)
        self.assertEqual([p["file_id"] for p in nalez], [2])

    def test_zaznam_bez_cisla_dilu_projde(self):
        """Neúplný záznam se nezahazuje — jazyk i díl se stejně pozná z textu při stažení."""
        api = self._api({"data": [titulek(7)]})
        with self.patch:
            nalez = api.hledej("tt9999999", ("CZ",), season=2, episode=1)
        self.assertEqual([p["file_id"] for p in nalez], [7])

    def test_poradi_otisk_jazyk_stazeni(self):
        """Otisk souboru přebíjí všechno; pak preferovaný jazyk, pak lidský překlad."""
        api = self._api({"data": [
            titulek(1, lang="sk", downloads=9000),
            titulek(2, lang="cs", downloads=5, machine=True),
            titulek(3, lang="cs", downloads=100),
            titulek(4, lang="sk", hash_match=True),
        ]})
        with self.patch:
            nalez = api.hledej("tt0133093", ("CZ", "SK"))
        self.assertEqual([p["file_id"] for p in nalez], [4, 3, 2, 1])

    def test_vypadek_site_nevyhodi_vyjimku(self):
        """Titulky jsou příslušenství — výpadek nesmí shodit výpis streamů."""
        api = OpenSubtitlesApi(KLIC)
        api._pauza = lambda: None
        with mock.patch.object(urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("sít spí")):
            self.assertEqual(api.hledej("tt0133093", ("CZ",)), [])

    def test_bez_klice_nic(self):
        api = OpenSubtitlesApi("")
        self.assertEqual(api.hledej("tt0133093", ("CZ",)), [])

    def test_kvota_ma_vlastni_vyjimku(self):
        """406 znamená vyčerpaný denní strop stahování — to jde uživateli říct."""
        api = OpenSubtitlesApi(KLIC)
        api._pauza = lambda: None
        chyba = urllib.error.HTTPError("u", 406, "Not Acceptable", {}, None)
        with mock.patch.object(urllib.request, "urlopen", side_effect=chyba):
            with self.assertRaises(KvotaVycerpana):
                api.odkaz(123)

    def test_odkaz_bez_linku_je_chyba(self):
        api = self._api({})
        with self.patch:
            with self.assertRaises(OpenSubtitlesError):
                api.odkaz(123)


class TestCache(unittest.TestCase):
    def test_druhy_dotaz_jde_z_cache(self):
        """Hledání je sice zadarmo, ale 5 dotazů za sekundu je strop — cache to drží."""
        with tempfile.TemporaryDirectory() as tmp:
            api = OpenSubtitlesApi(KLIC, store=Store(tmp))
            api._pauza = lambda: None
            volani = []

            def fake(req, timeout=None):
                volani.append(1)
                return Resp({"data": [titulek(5)]})

            with mock.patch.object(urllib.request, "urlopen", fake):
                prvni = api.hledej_cachovane("tt0133093", ("CZ",))
                druhy = api.hledej_cachovane("tt0133093", ("CZ",))
            self.assertEqual(prvni, druhy)
            self.assertEqual(len(volani), 1)

    def test_po_vypadku_se_sit_chvili_nezkousi(self):
        with tempfile.TemporaryDirectory() as tmp:
            api = OpenSubtitlesApi(KLIC, store=Store(tmp))
            api._pauza = lambda: None
            volani = []

            def fake(req, timeout=None):
                volani.append(1)
                raise urllib.error.URLError("nic")

            with mock.patch.object(urllib.request, "urlopen", fake):
                api.hledej("tt0133093", ("CZ",))
                api.hledej("tt0111161", ("CZ",))
            self.assertEqual(len(volani), 1)


if __name__ == "__main__":
    unittest.main()
