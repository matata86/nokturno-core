"""Klient obsahu z dashboardu (`nokturno_core/lib/dash_api.py`) a podobné tituly TMDB — bez sítě."""
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

from nokturno_core.lib import servers  # noqa: E402
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
            if urllib.parse.urlsplit(req.full_url).path.startswith(prefix):
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
            self.assertEqual(len(spadla.volani), len(servers.BASES),
                             "po výpadku (obě adresy serveru) se síť pět minut nezkouší")
        self.assertIsNotNone(self.store.peek_cached(DOWN_KEY, 300))

    def test_znacka_vypadku_neumlci_dotaz_bez_zalohy(self):
        """Značka výpadku smí ušetřit čekání jen tam, kde je co ukázat místo toho.
        Kodi na Androidu po startu chvíli nemá síť, zahřívání tam narazí a značka pak
        pět minut umlčela i výpisy otevřené rukou — 2026-09-23 to stálo celý rozcestník
        koncertů, i když síť mezitím dávno byla."""
        with mock.patch.object(urllib.request, "urlopen",
                               Sit({"/catalogs": urllib.error.URLError("bez site")})):
            self.assertEqual(self.api.menu(), [])          # nastaví značku výpadku
        self.assertIsNotNone(self.store.peek_cached(DOWN_KEY, 300))

        # jiná cesta, pro kterou klient nikdy nic neměl: musí se zkusit i teď
        zdrava = Sit({"/concerts/groups": {"version": 1, "artists": 2,
                                           "genres": [{"name": "rock", "artists": 2}],
                                           "letters": [{"name": "P", "artists": 2}]}})
        with mock.patch.object(urllib.request, "urlopen", zdrava):
            skupiny = self.api.concert_groups(("webshare",))
        self.assertEqual(len(zdrava.volani), 1, "bez záložních dat se síť zkusit musí")
        self.assertEqual(skupiny["genres"], [{"name": "rock", "artists": 2}])

    def test_znacka_vypadku_setri_cekani_tam_kde_je_zaloha(self):
        """Opak předchozího: se starými daty v ruce se na timeout nečeká."""
        with mock.patch.object(urllib.request, "urlopen", Sit({"/catalogs": MENU})):
            self.api.menu()
        with mock.patch("nokturno_core.lib.dash_api.MENU_TTL", 0):
            spadla = Sit({"/catalogs": urllib.error.URLError("timeout")})
            with mock.patch.object(urllib.request, "urlopen", spadla):
                self.assertEqual(len(self.api.menu()), 2)   # stará data
                pokusy = len(spadla.volani)
                self.assertEqual(len(self.api.menu()), 2)
                self.assertEqual(len(spadla.volani), pokusy, "podruhé se síť nezkouší")

    def test_bez_dat_a_bez_site_prazdno(self):
        with mock.patch.object(urllib.request, "urlopen", Sit({"/catalogs": urllib.error.URLError("x")})):
            self.assertEqual(self.api.menu(), [])


STROM = {"version": 1, "catalogs": [
    {"slug": "vanoce", "title": "Vánoce", "kind": "movie", "placement": "root", "icon": "christmas", "children": [
        {"slug": "komedie", "title": "Komedie", "kind": "movie", "placement": "root", "icon": "", "children": [
            {"slug": "ceske", "title": "České", "kind": "movie", "placement": "root", "icon": "", "children": [
                {"slug": "hluboko", "title": "Čtvrtá úroveň", "kind": "movie", "placement": "root"}]}]},
        {"slug": "rodinne", "title": "Rodinné", "kind": "movie", "placement": "root"},
        {"slug": "zlo", "title": "Bez slugu", "kind": "movie", "placement": "RunScript"},
    ]},
]}


class TestPodkategorie(unittest.TestCase):
    """Složky (`children`) — strom se čistí stejným whitelistem jako kořen a ořízne se."""

    def setUp(self):
        self.api = DashApi(cache=Store(tempfile.mkdtemp()))

    def test_strom_projde_whitelistem_a_orizne_se_na_max_depth(self):
        with mock.patch.object(urllib.request, "urlopen", Sit({"/catalogs": STROM})):
            menu = self.api.menu(placement="root")
        self.assertEqual([e["slug"] for e in menu], ["vanoce"])
        self.assertEqual([e["slug"] for e in menu[0]["children"]], ["komedie", "rodinne"])   # zlo zahozeno
        treti = menu[0]["children"][0]["children"]
        self.assertEqual([e["slug"] for e in treti], ["ceske"])
        self.assertEqual(treti[0]["children"], [], "čtvrtá úroveň se nebere")

    def test_group_najde_podkategorie_i_hloub(self):
        with mock.patch.object(urllib.request, "urlopen", Sit({"/catalogs": STROM})):
            self.assertEqual([e["slug"] for e in self.api.group("vanoce")], ["komedie", "rodinne"])
            self.assertEqual([e["slug"] for e in self.api.group("komedie")], ["ceske"])
            self.assertEqual(self.api.group("rodinne"), [])      # katalog bez podkategorií
            self.assertEqual(self.api.group("neexistuje"), [])
            self.assertEqual(self.api.group("../../etc"), [])


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


class TestMediaHlavicky(unittest.TestCase):
    """`DashApi.media()` — hlavičky souborů ze společné cache serveru."""

    def setUp(self):
        from nokturno_core.lib.store import Store
        self.api = DashApi(cache=Store(tempfile.mkdtemp()))

    def test_vrati_jen_trefy_spravneho_tvaru(self):
        odpoved = {"hits": {"ws:abc": {"audio": [{"lang": "cs"}], "height": 1080},
                            "ws:cizi": {"audio": [], "height": 0},      # prázdná hlavička
                            "ws:nechtel": {"height": 720}}}             # neptali jsme se
        with mock.patch.object(urllib.request, "urlopen", Sit({"/media": odpoved})):
            hits = self.api.media(["ws:abc", "ws:cizi", "hs:1:h", "dav:0:/x", "pt:1:s:h"])
        self.assertEqual(set(hits), {"ws:abc"})

    def test_posila_jen_sdilene_identy_a_nejvys_50(self):
        sit = Sit({"/media": {"hits": {}}})
        with mock.patch.object(urllib.request, "urlopen", sit):
            self.api.media([f"ws:{i}" for i in range(60)] + ["dav:0:/x"])
        dotaz = sit.volani[0]
        self.assertIn("ids=", dotaz)
        self.assertNotIn("dav", dotaz)
        self.assertEqual(dotaz.count("ws%3A"), 50)

    def test_vypadek_znamena_prazdno_a_pauzu(self):
        with mock.patch.object(urllib.request, "urlopen", Sit({"/media": urllib.error.URLError("x")})):
            self.assertEqual(self.api.media(["ws:abc"]), {})
        sit = Sit({"/media": {"hits": {"ws:abc": {"height": 1}}}})
        with mock.patch.object(urllib.request, "urlopen", sit):
            self.assertEqual(self.api.media(["ws:abc"]), {})   # DOWN_TTL: bez sítě
        self.assertEqual(sit.volani, [])

    def test_404_je_vypnuta_funkce(self):
        with mock.patch.object(urllib.request, "urlopen", Sit({"/media": urllib.error.HTTPError("u", 404, "x", {}, None)})):
            self.assertEqual(self.api.media(["ws:abc"]), {})


KONCERTY = {"version": 1, "artists": [
    {"id": 1, "name": "Pink Floyd", "concerts": 42},
    {"id": "x", "name": "rozbité"},
    {"id": 2, "name": "", "concerts": 1},
]}
INTERPRET = {"version": 1, "artist": {"id": 1, "name": "Pink Floyd"}, "concerts": [
    {"title": "Live in Venice", "year": 1989, "files": [
        {"source": "webshare", "ref": "ws:abc", "name": "PF Venice.avi", "size": 1000, "duration": 0},
        {"source": "hellspy", "ref": "hs:1:h", "name": "x", "size": 5},
        {"source": "napster", "ref": "np:1", "name": "cizí zdroj"},
        {"source": "webshare", "ref": "javascript:alert(1)", "name": "špatný odkaz"},
    ]},
    {"title": "Bez souborů", "year": None, "files": []},
    "smetí",
]}


class TestKoncerty(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())
        self.api = DashApi(cache=self.store)

    def test_interpreti_jen_platni_a_zdroje_v_klici(self):
        sit = Sit({"/concerts?": KONCERTY, "/concerts": KONCERTY})
        with mock.patch.object(urllib.request, "urlopen", sit):
            rows = self.api.concerts(["hellspy", "webshare", "napster"])
            self.api.concerts(["webshare", "hellspy"])           # stejná sada → cache
            self.api.concerts(["hellspy"])                       # jiná sada → nový dotaz
        self.assertEqual(rows, [{"id": 1, "name": "Pink Floyd", "concerts": 42,
                                 "genres": [], "letter": ""}])
        self.assertEqual(len(sit.volani), 2)
        self.assertIn("sources=hellspy%2Cwebshare", sit.volani[0])

    def test_interpret_jen_zname_odkazy(self):
        with mock.patch.object(urllib.request, "urlopen", Sit({"/concerts/1": INTERPRET})):
            data = self.api.concert_artist(1, ["webshare", "hellspy"])
        self.assertEqual(data["artist"], "Pink Floyd")
        self.assertEqual([c["title"] for c in data["concerts"]], ["Live in Venice"])
        self.assertEqual([f["ref"] for f in data["concerts"][0]["files"]], ["ws:abc", "hs:1:h"])
        self.assertIsNone(self.api.concert_artist("1", ["webshare"]))
        self.assertIsNone(self.api.concert_artist(-1, ["webshare"]))


POLOZKY = {"version": 1, "total": 2, "skip": 0, "items": [
    {"id": 7, "artist": "Pink Floyd", "title": "Pulse", "year": 1994, "sources": ["webshare", "napster"]},
    {"id": "x", "artist": "Nic", "title": "Nic"},
]}
KONCERT = {"version": 1, "id": 7, "artist": "Pink Floyd", "title": "Pulse", "year": 1994, "files": [
    {"source": "webshare", "ref": "ws:abc", "name": "pulse.mkv", "size": 5, "duration": 60},
    {"source": "webshare", "ref": "javascript:alert(1)", "name": "x"},
]}


NOVE = {"version": 1, "concerts": [
    {"id": 3, "artist": "Queen", "title": "Live At Wembley", "year": 1986, "files": [
        {"source": "webshare", "ref": "ws:q1", "name": "wembley.mkv", "size": 5},
        {"source": "webshare", "ref": "file:///etc/passwd", "name": "x"}]},
    {"id": 4, "artist": "", "title": "Bez interpreta", "files": [{"source": "webshare", "ref": "ws:x"}]},
    {"id": 5, "artist": "Kabát", "title": "Jen zlý odkaz", "files": [{"source": "napster", "ref": "ws:y"}]},
]}


class TestNovePridaneKoncerty(unittest.TestCase):
    def test_jen_platne_a_s_interpretem(self):
        api = DashApi(cache=Store(tempfile.mkdtemp()))
        with mock.patch.object(urllib.request, "urlopen", Sit({"/concerts/recent": NOVE})):
            data = api.concert_recent(["webshare"], install="abc")
        self.assertEqual([(c["artist"], c["title"], [f["ref"] for f in c["files"]]) for c in data],
                         [("Queen", "Live At Wembley", ["ws:q1"])])
        self.assertEqual(data[0]["id"], 3, "Stremio skládá z id `nktc:<id>`")

    def test_starsi_server_bez_cesty(self):
        api = DashApi(cache=Store(tempfile.mkdtemp()))
        with mock.patch.object(urllib.request, "urlopen", Sit({})):
            self.assertEqual(api.concert_recent(["webshare"]), [])


class TestKoncertyPloche(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())
        self.api = DashApi(cache=self.store)

    def test_polozky_a_detail(self):
        sit = Sit({"/concerts/items": POLOZKY, "/concerts/item/7": KONCERT})
        with mock.patch.object(urllib.request, "urlopen", sit):
            items, total = self.api.concert_items(["webshare"], search="pul", skip=0)
            data = self.api.concert(7, ["webshare"])
            self.assertIsNone(self.api.concert("7", ["webshare"]))
        self.assertEqual(total, 2)
        self.assertEqual(items, [{"id": 7, "artist": "Pink Floyd", "title": "Pulse", "year": 1994, "sources": ["webshare"]}])
        self.assertIn("search=pul", sit.volani[0])
        self.assertEqual([f["ref"] for f in data["files"]], ["ws:abc"])
        self.assertEqual(data["title"], "Pulse")
