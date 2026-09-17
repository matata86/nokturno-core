"""Klient obsahu z dashboardu (`nokturno_core/lib/dash_api.py`) a podobné tituly TMDB — bez sítě."""
import json
import pathlib
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib.dash_api import DOWN_KEY, DashApi  # noqa: E402
from nokturno_core.lib.store import Store                 # noqa: E402
from nokturno_core.lib.tmdb_api import TmdbApi            # noqa: E402


class Resp:
    def __init__(self, data):
        self.body = json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


MENU = {"version": 1, "catalogs": [
    {"slug": "harry-potter", "title": "Harry Potter", "kind": "movie", "placement": "root", "icon": "star"},
    {"slug": "vanoce", "title": "[COLOR red]Vánoce[/COLOR]", "kind": "series", "placement": "browse",
     "icon": "http://zlo/x.png"},
    {"slug": "../../etc", "title": "Zlo", "kind": "movie", "placement": "root"},
    {"slug": "exec", "title": "Neznámé umístění", "kind": "movie", "placement": "RunScript"},
    {"slug": "anime", "title": "Neznámý druh", "kind": "anime", "placement": "root"},
    "nesmysl",
]}


class Sit:
    """Podstrčený urlopen: slovník cesta → odpověď (dict / výjimka), počítá volání."""

    def __init__(self, odpovedi):
        self.odpovedi = odpovedi
        self.volani = []

    def __call__(self, req, timeout=None):
        self.volani.append(req.full_url)
        for prefix, odpoved in self.odpovedi.items():
            if req.full_url.split("nokturno.tailf0014.ts.net", 1)[1].startswith(prefix):
                if isinstance(odpoved, Exception):
                    raise odpoved
                return Resp(odpoved)
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)


class TestMenu(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())
        self.api = DashApi(cache=self.store)

    def test_whitelist_zahodi_neznama_umisteni_druhy_a_slugy(self):
        with mock.patch.object(urllib.request, "urlopen", Sit({"/catalogs": MENU})):
            menu = self.api.menu()
        self.assertEqual([e["slug"] for e in menu], ["harry-potter", "vanoce"])
        self.assertEqual(menu[1]["title"], "COLOR redVánoce/COLOR")   # žádné formátovací značky Kodi
        self.assertEqual(menu[1]["icon"], "")                          # neznámá ikona → výchozí

    def test_filtr_umisteni_a_tvar_katalogu(self):
        with mock.patch.object(urllib.request, "urlopen", Sit({"/catalogs": MENU})):
            self.assertEqual([e["slug"] for e in self.api.menu(placement="browse")], ["vanoce"])
            self.assertEqual(self.api.catalogs("movie"), [
                {"id": "harry-potter", "name": "Harry Potter", "search": False, "genre_required": False,
                 "genres": []}])

    def test_cache_a_vypadek_vrati_posledni_data_a_nezkousi_sit(self):
        sit = Sit({"/catalogs": MENU})
        with mock.patch.object(urllib.request, "urlopen", sit):
            self.api.menu()
            self.api.menu()
        self.assertEqual(len(sit.volani), 1, "druhé volání z cache")
        # prošlá cache + výpadek → stará data, značka výpadku
        with mock.patch("nokturno_core.lib.dash_api.MENU_TTL", 0):
            spadla = Sit({"/catalogs": urllib.error.URLError("timeout")})
            with mock.patch.object(urllib.request, "urlopen", spadla):
                self.assertEqual(len(self.api.menu()), 2)
                self.assertEqual(len(self.api.menu()), 2)
            self.assertEqual(len(spadla.volani), 1, "po výpadku se síť pět minut nezkouší")
        self.assertIsNotNone(self.store.peek_cached(DOWN_KEY, 300))

    def test_bez_dat_a_bez_site_prazdno(self):
        with mock.patch.object(urllib.request, "urlopen", Sit({"/catalogs": urllib.error.URLError("x")})):
            self.assertEqual(self.api.menu(), [])


class TestObsah(unittest.TestCase):
    def setUp(self):
        self.api = DashApi(cache=Store(tempfile.mkdtemp()))

    def test_katalog_jen_platna_id(self):
        sit = Sit({"/catalogs/harry-potter": {"slug": "harry-potter", "items": [
            {"id": "tt0241527", "name": "Kámen mudrců", "year": "2001"}, {"id": "javascript:x", "name": "zlo"}]}})
        with mock.patch.object(urllib.request, "urlopen", sit):
            items = self.api.catalog("movie", "harry-potter")
            self.assertEqual(self.api.catalog("movie", "../x"), [])
            self.assertEqual(self.api.catalog("movie", "neni"), [])
        self.assertEqual([(i["id"], i["type"], i["_title"]) for i in items],
                         [("tt0241527", "movie", "Kámen mudrců")])
        self.assertEqual(len(sit.volani), 2, "neplatný slug nejde na síť")

    def test_podobne(self):
        sit = Sit({"/similar": {"items": [{"id": "tt0234215", "name": "Matrix Reloaded"}]}})
        with mock.patch.object(urllib.request, "urlopen", sit):
            self.assertEqual([i["id"] for i in self.api.similar("movie", "tt0133093")], ["tt0234215"])
            self.assertEqual(self.api.similar("movie", "sosac:123"), [])
        self.assertIn("kind=movie&id=tt0133093", sit.volani[0])

    def test_tv_program(self):
        data = {"today": "2026-09-17", "date": "2026-09-17", "dates": ["2026-09-17", "zlo"],
                "channels": [{"slug": "ct1", "name": "ČT1"}, {"slug": "../", "name": "x"}],
                "items": [
                    {"channel": "ct1", "channel_name": "ČT1", "start": 1789668000, "stop": 1789674000,
                     "kind": "movie", "title": "Pelíšky", "meta": {"id": "tt0167331", "name": "Pelíšky"}},
                    {"channel": "ct1", "channel_name": "ČT1", "start": 1, "stop": 2, "kind": "movie",
                     "title": "Bez meta", "meta": None},
                    {"channel": "ct1", "start": "x", "stop": 2, "kind": "movie", "meta": {"id": "tt0167331"}},
                ]}
        sit = Sit({"/tv-program": data})
        with mock.patch.object(urllib.request, "urlopen", sit):
            tv = self.api.tv_program("2026-09-17", kind="movie", channel="ct1")
            self.api.tv_program("zítra; drop", kind="sport", channel="../")
        self.assertEqual([i["title"] for i in tv["items"]], ["Pelíšky"])
        self.assertEqual(tv["items"][0]["meta"]["type"], "movie")
        self.assertEqual((tv["dates"], [c["slug"] for c in tv["channels"]]), (["2026-09-17"], ["ct1"]))
        self.assertTrue(sit.volani[1].endswith("/tv-program"), "neplatné filtry se neposílají")


class TestTmdbPodobne(unittest.TestCase):
    def test_doporuceni_doplnena_podobnymi_bez_sebe_sama(self):
        api = TmdbApi("k")

        def get(path, **params):
            if path.startswith("/genre/"):
                return {"genres": []}
            if path.startswith("/find/"):
                return {"movie_results": [{"id": 603}]}
            if path == "/movie/603/recommendations":
                return {"results": [{"id": 604, "title": "Reloaded"}, {"id": 603, "title": "Matrix"}]}
            if path == "/movie/603/similar":
                return {"results": [{"id": 604}, {"id": 605, "title": "Revolutions"}, {"id": 1}]}
            if path.startswith("/movie/"):
                tmdb_id = path.rsplit("/", 1)[1]
                return {"external_ids": {"imdb_id": "" if tmdb_id == "1" else f"tt0{tmdb_id}"}}
            raise AssertionError(path)
        api._get = get
        self.assertEqual([i["id"] for i in api.similar("movie", "tt0133093")], ["tt0604", "tt0605"])

    def test_nenalezeny_titul(self):
        api = TmdbApi("k")
        api._get = lambda path, **p: {"genres": []} if path.startswith("/genre/") else {"tv_results": []}
        self.assertEqual(api.similar("series", "tt0000001"), [])


if __name__ == "__main__":
    unittest.main()
