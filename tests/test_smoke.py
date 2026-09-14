"""Kontrola, že jádro drží, co na něm stojí ostatní.

Bez externích závislostí, aby šlo pustit kdekoli:

    python3 -m unittest discover -s tests -v

Nesahá na síť. Ověřuje jen tvar jádra a to, co na něm konzumenti vyžadují —
tedy věci, které se při rozesílání dají tiše rozbít.
"""
import ast
import json
import inspect
import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core import Engine, split_episode_id                    # noqa: E402
from nokturno_core.lib import const, streams, enrich, webshare_api    # noqa: E402


class TestJadroJeSamostatne(unittest.TestCase):
    """Jádro nesmí záviset na hostiteli, jinak ho nepřevezme další konzument."""

    def test_zadny_import_hostitele(self):
        for path in sorted(ROOT.glob("nokturno_core/**/*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    self.assertNotIn(root, ("xbmc", "xbmcaddon", "xbmcgui", "xbmcplugin",
                                            "xbmcvfs", "homeassistant"),
                                     f"{path.name} importuje {name}")

    def test_engine_bez_nastaveni(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({}, tmp)
            self.assertEqual(engine.sources(),
                             {"luna": False, "sosac": False, "webshare": False,
                              "hellspy": False, "sledujteto": False, "storage": False, "torrent": False})

    def test_rozpad_id_epizody(self):
        self.assertEqual(split_episode_id("tt0903747:2:5"), ("tt0903747", 2, 5))


class TestCoVyzadujeKodi(unittest.TestCase):
    """`default.py` a `service.py` doplňku importují lib ploše a jmenovitě."""

    IMPORTY = {
        "luna_api": ("LunaApi", "LunaError", "parse_base_url", "parse_token"),
        "cinemeta_api": ("CinemetaApi", "CinemetaError"),
        "tmdb_api": ("TmdbApi", "TmdbError"),
        "sosac_api": ("SosacError", "is_sosac_id", "names_match"),
        "sosac_direct": ("EXPORT", "SosacDirect", "is_direct_id"),
        "enrich": ("enrich", "enrich_one"),
        "hellspy_api": ("HellspyApi", "HellspyError"),
        "sledujteto_api": ("SledujtetoApi", "SledujtetoError"),
        "mediainfo": ("describe", "probe", "quality_from_size"),
        "store": ("Store", "migrate_profile"),
        "sync": ("sync_once",),
        "streams": ("arrange", "estimate_rank", "langs_from_name", "parse_stream", "subs_from_name"),
        "trakt_api": ("TraktApi", "TraktError"),
        "webshare_api": ("SORTS", "WebshareApi", "WebshareError", "human_size"),
        "stats": ("COLLECT_URL", "Stats"),
    }

    def test_vsechny_symboly_existuji(self):
        import importlib
        for modul, symboly in self.IMPORTY.items():
            m = importlib.import_module(f"nokturno_core.lib.{modul}")
            for symbol in symboly:
                self.assertTrue(hasattr(m, symbol), f"{modul}.{symbol} chybí — rozbije doplněk pro Kodi")


class TestZpetnaKompatibilita(unittest.TestCase):
    """Co přibylo sloučením větví, nesmí vynutit změnu u volajících."""

    def test_enrich_ma_callbacky_volitelne(self):
        params = inspect.signature(enrich.enrich).parameters
        self.assertEqual(list(params)[:5], ["metas", "luna", "store", "ctype", "deadline"])
        for name in ("on_tick", "on_count"):
            self.assertIsNone(params[name].default, f"{name} musí být volitelný")

    def test_webshare_bez_cache(self):
        api = webshare_api.WebshareApi("uzivatel", "heslo")
        self.assertIsNone(api.cache, "cache musí zůstat volitelná, HA ji nepředává")


class TestRazeniStreamu(unittest.TestCase):
    # jazyky plní `parse_stream` z pole `detail` („Zvuk: CZ 5.1“), ne ze vstupního
    # `langs` — stream bez něj si je nanejvýš odhaduje z názvu souboru
    OVERENO = "Zvuk: CZ 5.1"

    def test_odhadnute_patri_za_overene(self):
        """Remízový klíč `verified`: při shodné kvalitě jde napřed ten s jazykem od zdroje."""
        odhad = {"label": "1080p", "url": "a", "detail": ""}
        overeno = {"label": "1080p", "url": "b", "detail": self.OVERENO}
        poradi = streams.arrange([odhad, overeno], order="quality")
        self.assertEqual(poradi[0]["url"], "b")
        self.assertEqual(poradi[1]["url"], "a")

    def test_kvalita_prebiji_jazyk(self):
        """Odhadnuté 4K nesmí spadnout pod ověřené SD — `verified` je až druhý klíč."""
        sd = {"label": "SD", "url": "sd", "detail": self.OVERENO}
        uhd = {"label": "4K", "url": "4k", "detail": ""}
        self.assertEqual(streams.arrange([sd, uhd], order="quality")[0]["url"], "4k")

    def test_titulky_z_nazvu_souboru(self):
        self.assertEqual(streams.subs_from_name("film_cz_tit_1080p"), {"CZ"})
        self.assertEqual(streams.subs_from_name("film.CZtit.1080p"), {"CZ"})


class TestKonstanty(unittest.TestCase):
    def test_klice_odpovidaji_tomu_co_engine_cte(self):
        """Engine čte klíče většinou řetězcem, jen `CONF_HS_ENABLED` konstantou.

        Překlep v hodnotě by se jinak neprojevil chybou, jen tichým ignorováním
        nastavení. Sjednotit engine na konstanty je úklid na jindy.
        """
        zdroj = (ROOT / "nokturno_core" / "engine.py").read_text(encoding="utf-8")
        chybi = [j for j in dir(const) if j.startswith("CONF_")
                 and f'"{getattr(const, j)}"' not in zdroj and j not in zdroj]
        self.assertEqual(chybi, [], f"klíče, které engine nezná: {chybi}")

    def test_vychozi_razeni_je_povolene(self):
        self.assertIn(const.DEFAULT_SORT, const.SORT_ORDERS)


class TestStats(unittest.TestCase):
    """`note_play`/`note_use` volají klienti (Kodi, HA) přes `hass.async_add_executor_job`
    nebo přímo — výjimka uvnitř by se jinak potichu spolkla (klienti ji záměrně
    nenechají spadnout celou odpověď) a zjistilo by se to až z chybějících dat
    na Dashboardu. Skutečné volání přes `Stats` proto musí projít, ne se jen
    zkontrolovat čtením zdrojáku."""

    def setUp(self):
        from nokturno_core.lib.stats import Stats
        self.tmp = tempfile.mkdtemp()
        self.stats = Stats(self.tmp)

    def test_note_play_a_payload(self):
        self.stats.note_use()
        self.stats.note_play("tt0133093", "Matrix", 1999, "movie")
        payload = self.stats.payload(version="1.0", platform="Linux")
        plays = {p["key"]: p for p in payload["plays"]}
        self.assertIn("tt0133093", plays)
        self.assertEqual(plays["tt0133093"]["t"], "Matrix")
        self.assertEqual(plays["tt0133093"]["y"], 1999)
        self.assertIsInstance(plays["tt0133093"]["l"], int)

    def test_zdroje_a_produkt_v_hlaseni(self):
        payload = self.stats.payload(version="3.0.0", sources=["webshare", "sledujteto", "", "webshare"],
                                     product="stremio")
        self.assertEqual(payload["sources"], ["sledujteto", "webshare"])
        self.assertEqual(payload["product"], "stremio")
        self.assertNotIn("sources", self.stats.payload(), "starší volání bez zdrojů je nepošle vůbec")

    def test_ping_bez_titulu_a_zdroju(self):
        """Vypnuté statistiky: jen id, produkt a verze — nic, co by šlo o používání vyčíst."""
        self.stats.note_use()
        self.stats.note_play("tt0133093", "Matrix", 1999, "movie")
        ping = self.stats.ping_payload(version="3.1.9", product="kodi")
        self.assertEqual(set(ping), {"id", "ping", "version", "product"})
        self.assertTrue(ping["ping"])
        sent = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n=-1): return b""
            def getcode(self): return 200

        import urllib.request
        from unittest import mock
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: (sent.update(json.loads(req.data)), Resp())[1]):
            self.assertEqual(self.stats.send("https://x/collect", version="3.1.9", product="kodi", ping=True), (True, ""))
        self.assertEqual(sent, ping)

    def test_note_play_bez_titulu_nespadne(self):
        """`title=""`/`year=None` je běžný stav (dohledání meta selhalo) — nesmí to shodit."""
        self.stats.note_play("sosacd_m_x")
        self.assertIn("sosacd_m_x", self.stats.data["plays"])


class TestSlucovaniPrimychStreamu(unittest.TestCase):
    def test_osamocene_soubory_se_neorezavaji(self):
        """Soubory z WebShare/HellSpy bez protějšku v Luně musí zůstat všechny.

        Strop 8 dřív v HA schoval většinu streamů (Toy Story 5: 17 proti 64 v Kodi).
        """
        direct = [{"label": f"Film.2026.{i}.mkv", "detail": f"{1 + i / 10:.1f} GB",
                   "source": "ws" if i % 2 else "hs", "url": f"ws:{i}", "_direct": True}
                  for i in range(30)]
        for s in direct:
            streams.parse_stream(s)
        self.assertEqual(len(Engine._merge_direct(direct)), 30)

    def test_prisny_filtr_s_kratkym_slovem_v_nazvu(self):
        """„Harry Potter a Kámen mudrců": krátké „a" nesmí shodit přesnou shodu."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({}, tmp)
            _dotazy, relevant = engine._title_queries(
                {"name": "Harry Potter a Kámen mudrců", "year": 2001}, None, "movie", None, True)
            self.assertTrue(relevant("Harry.Potter.a.Kámen.mudrců.(2001) CZ DABING"))
            self.assertTrue(relevant("Harry Potter a Kamen mudrcu (2001) (prodlouzena verze)"))
            self.assertFalse(relevant("Harry Potter a Tajemná komnata (2002)"))

    def _relevant(self, name, year, orig=""):
        with tempfile.TemporaryDirectory() as tmp:
            return Engine({}, tmp)._title_queries({"name": name, "year": year, "_orig": orig},
                                                None, "movie", None, True)[1]

    def test_kratky_nazev_neprojde_uprostred_jineho(self):
        """„To" (2017) dřív nemělo filtr slov vůbec — prošlo cokoli bez roku."""
        relevant = self._relevant("To", 2017, "It")
        self.assertTrue(relevant("To.2017.1080p.CZ.dabing.mkv"))
        self.assertTrue(relevant("To (2017) CZ"))
        self.assertTrue(relevant("To - It (2017) CZ Dabing"), "dvojí název titulu")
        for jine in ("What Happened to Monday (2017)", "Někdo to rád horké", "Jumanji 2017",
                     "Tora! Tora! Tora!", "To Kapitola 2"):
            self.assertFalse(relevant(jine), jine)

    def test_pokracovani_neprojde(self):
        relevant = self._relevant("Jak vycvičit draka", 2010)
        self.assertTrue(relevant("Jak vycvičit draka (2010) CZ 5.1"))
        self.assertTrue(relevant("Jak.vycvicit.draka.5.1.CZ.mkv"), "zvuk 5.1 není díl")
        for jine in ("Jak vycvičit draka 2", "Jak.vycvicit.draka.3.CZ", "Jak_vycvicit_draka_2_2014"):
            self.assertFalse(relevant(jine), jine)
        padouch = self._relevant("Já, padouch", 2010)
        self.assertFalse(padouch("Despicable.Me.2.Puppy.mkv"))
        self.assertFalse(padouch("Ja padouch II"))

    def test_cislo_dilu_v_nazvu_titulu(self):
        relevant = self._relevant("Toy Story 5", 2026)
        self.assertTrue(relevant("Toy.Story.5.2026.1080p"))
        self.assertFalse(relevant("Toy Story 2"))

    def test_idiom_s_nazvem_uprostred_neprojde(self):
        """„Pět švestek“ (2026) × „Seber si svých pět švestek“ (1983) — prošlo v kartě HA."""
        for nazev in ("Pět švestek", "Pet svestek"):
            relevant = self._relevant(nazev, 2026)
            for jine in ("Seber si svych pet svestek r1983 Pierre Richard CZ", "Seber si svých pět švestek"):
                self.assertFalse(relevant(jine), (nazev, jine))
            for ok in ("Pět švestek (2026) CZ 1080p", "Pet.svestek.2026.WEB-DL", "[CSFD] Pet svestek 2026",
                       "CZ Dabing - Pet svestek 2026", "www.film.cz | Pet svestek 2026"):
                self.assertTrue(relevant(ok), (nazev, ok))
        certi = self._relevant("S čerty nejsou žerty", 1984)
        for ok in ("S čerty nejsou žerty (1984) CZ", "S certy nejsou zerty Cz Dabing 1985.mp4",
                   "S čerty nejsou žerty CZ Dabing 1985"):
            self.assertTrue(certi(ok), ok)

    def test_rok_za_podtrzitkem(self):
        relevant = self._relevant("Jak vycvičit draka", 2010)
        self.assertFalse(relevant("Jak_vycvicit_draka_2025"))
        self.assertTrue(relevant("Jak_vycvicit_draka_2010_CZ"))



class TestStoreSdilenyViceProcesy(unittest.TestCase):
    """Doplněk v Kodi je nový proces na každý výpis, k tomu služba na pozadí — všichni
    nad týmiž soubory. Dvě instance `Store` nad jednou složkou tu hrají dva procesy."""

    def setUp(self):
        from nokturno_core.lib.store import Store
        self.tmp = tempfile.mkdtemp()
        self.a, self.b = Store(self.tmp), Store(self.tmp)

    def test_zapis_z_jineho_procesu_se_neprepise(self):
        """A si soubor načte, B do něj přidá snímek, A přidá svůj — snímek od B musí zůstat.

        Přesně takhle výpis katalogu Sosáče (rejstřík `idx:`) přepsal snímek,
        který mezitím uložilo přehrání, a titul se v Pokračovat neukázal."""
        self.a.item("x")                                   # A má items v paměti
        time.sleep(0.01)
        self.b.remember_item("tt38061210", {"title": "Why Did I Get Married Again?"})
        time.sleep(0.01)
        self.a.remember_item("tt1", {"title": "jiný"})
        self.assertIsNotNone(self.b.item("tt38061210"))
        self.assertIsNotNone(self.b.item("tt1"))

    def test_rozkoukane_ze_sluzby_nevrati_odebrane(self):
        """Služba drží `watched` dlouho; když mezitím doplněk titul z Pokračovat
        odebral, zápis pozice jiného titulu ze služby ho nesmí vrátit."""
        self.a.set_resume("film1", 100, 1000)
        self.b.in_progress()                               # B (služba) má watched v paměti
        time.sleep(0.01)
        self.a.set_resume("film1", 0, 0)                   # odebráno z Pokračovat
        time.sleep(0.01)
        self.b.set_resume("film2", 200, 1000)
        self.assertEqual([k for k, _v in self.a.in_progress()], ["film2"])

    def test_rejstrik_sosace_ma_vlastni_soubor(self):
        self.a.remember_item("tt1", {"title": "film"})
        self.a.remember_item("idx:sosacd_m_1", {"name": "starý rejstřík"})   # jako před 2.0.23
        index = self.b.index()
        self.assertEqual(index.item("idx:sosacd_m_1")["name"], "starý rejstřík")
        self.assertNotIn("idx:sosacd_m_1", self.b.load("items", {}), "odstěhováno z items.json")
        index.remember_item("idx:sosacd_m_2", {"name": "nový"})
        self.assertEqual(set(self.a.load("items", {})), {"tt1"})
        self.assertEqual(index.item("tt1")["title"], "film", "snímek přehraného titulu přes rejstřík")
        self.assertTrue(pathlib.Path(self.tmp, "sosac_index.json").exists())

    def test_vypis_sosace_zapise_rejstrik_jednou(self):
        """Rejstřík má přes megabajt — zápis po položkách dělal na Office z 47 filmů 45 s."""
        from nokturno_core.lib.sosac_direct import SosacDirect
        index = self.a.index()
        saves = []
        orig = self.a.save
        self.a.save = lambda name, data: (saves.append(name), orig(name, data))
        d = SosacDirect(index_store=index)
        d._get = lambda url, ttl=None: [{"n": {"cs": f"Film {i}"}, "m": str(i), "l": f"l{i}"} for i in range(5)]
        self.assertEqual(len(d.catalog("movie", "moviesmostpopular")), 5)
        self.assertEqual(saves.count("sosac_index"), 1)
        self.assertEqual(index.item("idx:sosacd_m_l3")["name"], "Film 3")
        d.catalog("movie", "moviesmostpopular")
        self.assertEqual(saves.count("sosac_index"), 1, "stejný seznam podruhé bez zápisu")
        d._get = lambda url, ttl=None: [{"n": {"cs": "Nový film"}, "m": "9", "l": "l9"}]
        d.catalog("movie", "moviesmostpopular")
        self.assertEqual(saves.count("sosac_index"), 2, "nový titul se zapíše")

    def test_zalozeni_rejstriku_nesaha_na_disk(self):
        """HA zakládá rejstřík z atributů senzoru ve smyčce událostí — tam žádné `open()`."""
        self.a.remember_item("idx:sosacd_m_1", {"name": "starý rejstřík"})
        pred = sorted(p.name for p in pathlib.Path(self.tmp).iterdir())
        from nokturno_core.lib.store import Store
        Store(self.tmp).index()
        self.assertEqual(sorted(p.name for p in pathlib.Path(self.tmp).iterdir()), pred)
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"streamuj_username": "u", "streamuj_password": "p"}, tmp)
            before = sorted(p.name for p in pathlib.Path(tmp).iterdir())
            engine.sources()
            self.assertEqual(sorted(p.name for p in pathlib.Path(tmp).iterdir()), before)



class TestVypadekZdroje(unittest.TestCase):
    """Výpadek jednoho zdroje (vypnutý addon Luny) nesmí shodit hledání v ostatních."""

    def _engine(self, tmp):
        from nokturno_core.lib.luna_api import LunaApi, LunaError
        engine = Engine({}, tmp)

        class MrtvaLuna(LunaApi):
            def __init__(self):
                pass

            def streams(self, *a, **k):
                raise LunaError("<urlopen error [Errno 111] Connection refused> "
                                "(http://192.168.1.10:7126/e1.tajnytoken/stream/movie/tt1.json)")

        engine.api_for = lambda item_id: MrtvaLuna()
        engine.meta = lambda ctype, item_id, series_id=None: ({"id": item_id, "name": "Film", "year": 2020}, None)
        engine._cross_streams = lambda *a, **k: []
        engine._webshare_subtitles = lambda *a, **k: []
        engine._fill_audio = lambda streams, *a, **k: streams
        engine._hellspy_streams = lambda *a, **k: []
        engine._webshare_streams = lambda *a, **k: [
            {"url": "ws:abc", "label": "Film.2020.1080p.CZ.mkv", "detail": "4.2 GB", "source": "ws", "_direct": True}]
        return engine

    def test_luna_mimo_provoz_ostatni_zdroje_jedou(self):
        from nokturno_core.lib.source_errors import summarize
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            failures = []
            found = engine.streams("movie", "tt1", failures=failures)
            self.assertEqual(len(found), 1, "stream z WebShare musí zůstat")
            self.assertEqual([label for label, _e in failures], ["Luna"])
            self.assertEqual(summarize(failures), ["Luna neodpovídá"])

    def test_vysledek_s_vypadkem_se_necachuje(self):
        """Jinak by po návratu Luny její streamy chyběly 72 hodin."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            engine.streams("movie", "tt1", failures=[])
            engine.api_for = lambda item_id: None   # Luna zpátky (tady: bez chyby, nic nevrátí)
            druhe = []
            engine.api_for = lambda item_id: type("L", (), {"streams": lambda self, *a, **k: []})()
            engine.streams("movie", "tt1", failures=druhe)
            self.assertEqual(druhe, [], "druhé hledání se muselo opravdu provést, ne vzít z cache")
            cache = list(pathlib.Path(tmp, "cache").glob("*.json"))
            self.assertTrue(cache, "úspěšný výsledek bez výpadku se cachuje")


class TestPopisVypadku(unittest.TestCase):
    def test_bez_adresy_a_tokenu(self):
        from nokturno_core.lib.source_errors import describe_failure
        chyba = "<urlopen error [Errno 111] Connection refused> (http://192.168.1.10:7126/e1.tajny/stream/x.json)"
        self.assertEqual(describe_failure("Luna", chyba), "Luna neodpovídá")
        self.assertEqual(describe_failure("WebShare", "login: Wrong password"), "WebShare: login: Wrong password")
        self.assertNotIn("tajny", describe_failure("Luna", "divná chyba https://x/e1.tajny/y"))



class TestSledujteto(unittest.TestCase):
    """Klient Sledujteto bez sítě — odpovědi API ve tvaru z jejich doplňku pro Kodi."""

    def setUp(self):
        from nokturno_core.lib import sledujteto_api as st
        from nokturno_core.lib.store import Store
        self.st = st
        self.tmp = tempfile.mkdtemp()
        self.store = Store(self.tmp)
        self.calls = []
        self._orig = st.urllib.request.urlopen

    def tearDown(self):
        self.st.urllib.request.urlopen = self._orig

    def _server(self, handler):
        import io, urllib.error
        test = self

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def urlopen(req, timeout=None):
            auth = req.headers.get("Authorization", "")
            test.calls.append((req.get_method(), req.full_url.split("/api/", 1)[1], auth))
            status, body = handler(req.get_method(), req.full_url.split("/api/", 1)[1], auth,
                                   json.loads(req.data) if req.data else None)
            data = json.dumps(body).encode()
            if status >= 400:
                raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(data))
            return Resp(data)
        self.st.urllib.request.urlopen = urlopen

    VIDEO = {"id": 123, "name": "Matrix (1999) CZ dabing 1080p", "is_premium": False,
             "video": {"is_hd": True, "is_4k": False, "duration": "2:16:17", "thumb_urls": ["https://x/t.jpg"],
                       "subtitles": [{"url": "https://x/s.srt"}], "size": 4500000000}}

    def test_prihlaseni_hledani_a_odkaz(self):
        def handler(method, path, auth, body):
            if path == "v1/token":
                self.assertEqual(body, {"email": "a@b.cz", "password": "tajne"})
                return 200, {"data": {"token": "T1", "expires": ""}}
            self.assertEqual(auth, "Bearer T1")
            if path.startswith("v1/videos"):
                return 200, {"data": {"results": [self.VIDEO], "total": 1}}
            if path == "v1/video/123/link":
                return 200, {"data": {"link": "https://cdn/x.mp4", "id": 9}}
            return 404, {}
        self._server(handler)
        api = self.st.SledujtetoApi("a@b.cz", "tajne", cache=self.store)
        files, total = api.search("matrix")
        self.assertEqual(total, 1)
        f = files[0]
        self.assertEqual((f["id"], f["quality"], f["duration"], f["size"]), ("123", "HD", 8177, 4500000000))
        self.assertEqual(f["subtitles"], ["https://x/s.srt"])
        self.assertIn("video.size", api.last_keys)
        self.assertEqual(api.file_link("123"), "https://cdn/x.mp4")
        # druhé jádro nad stejným úložištěm se znovu nepřihlašuje
        api2 = self.st.SledujtetoApi("a@b.cz", "tajne", cache=self.store)
        api2.file_link("123")
        self.assertEqual(sum(1 for c in self.calls if c[1] == "v1/token"), 1)

    def test_po_401_se_prihlasi_znovu(self):
        tokens = iter(["STARY", "NOVY"])
        def handler(method, path, auth, body):
            if path == "v1/token":
                return 200, {"data": {"token": next(tokens)}}
            return (401, {"data": {"message": "Unauthorized"}}) if auth == "Bearer STARY" \
                else (200, {"data": {"link": "https://cdn/y.mp4"}})
        self._server(handler)
        api = self.st.SledujtetoApi("a@b.cz", "tajne", cache=self.store)
        self.assertEqual(api.file_link("5"), "https://cdn/y.mp4")

    def test_bez_premium_srozumitelna_chyba(self):
        def handler(method, path, auth, body):
            if path == "v1/token":
                return 200, {"data": {"token": "T"}}
            return 403, {"data": {"message": "Forbidden"}}
        self._server(handler)
        with self.assertRaises(self.st.SledujtetoError) as ctx:
            self.st.SledujtetoApi("a@b.cz", "tajne").file_link("1")
        self.assertIn("Premium", str(ctx.exception))

    def test_spatne_heslo(self):
        self._server(lambda m, p, a, b: (401, {"data": {"errors": ["invalid_credentials"]}}))
        with self.assertRaises(self.st.SledujtetoError) as ctx:
            self.st.SledujtetoApi("a@b.cz", "spatne").search("x")
        self.assertIn("e-mail a heslo", str(ctx.exception))
        self.assertNotIn("spatne", str(ctx.exception))



class TestSledujtetoVJadru(unittest.TestCase):
    def test_streamy_filtr_a_prehrani(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"st_email": "a@b.cz", "st_password": "x"}, tmp)
            self.assertTrue(engine.sources()["sledujteto"])

            class Fake:
                last_keys = ["id", "name", "video.size"]

                def search(self, query, limit=25, offset=0):
                    return ([{"id": "1", "name": "Matrix (1999) CZ dabing", "size_h": "4.00 GB",
                              "quality": "HD", "duration": 8177, "subtitles": ["https://x/s.srt"]},
                             {"id": "2", "name": "Matrix Reloaded (2003)", "size_h": "", "quality": ""}], 2)

                def file_link(self, video_id):
                    return f"https://cdn/{video_id}.mp4"

            engine._st = Fake()
            found = engine._sledujteto_streams({"name": "Matrix", "year": 1999}, None, "movie")
            self.assertEqual([s["url"] for s in found], ["st:1"], "Reloaded je jiný titul")
            self.assertEqual(found[0]["subtitles"], ["https://x/s.srt"])
            self.assertEqual(engine.resolve("st:1"), "https://cdn/1.mp4")

    def test_bez_hesla_neni_zdroj(self):
        from nokturno_core import NokturnoError
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"st_email": "a@b.cz"}, tmp)
            self.assertIsNone(engine.st)
            with self.assertRaises(NokturnoError):
                engine.resolve("st:1")



class TestSledujtetoMedia(unittest.TestCase):
    def test_kanaly_kodek_a_rozliseni_z_api(self):
        from nokturno_core.lib.sledujteto_api import media
        self.assertEqual(media({"audio_channels": 6, "audio_codec": "eac3", "resolution": "1920x1080"}),
                         {"audio": [{"lang": "", "channels": "5.1", "codec": "EAC3"}], "subs": [],
                          "width": 1920, "height": 1080})
        self.assertEqual(media({"audio_channels": "2.0", "resolution": "720p"})["audio"][0]["channels"], "2.0")
        self.assertEqual(media({"audio_channels": "5.1"})["audio"][0]["channels"], "5.1")
        self.assertEqual(media({})["audio"], [])

    def test_popis_nese_kodek(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({}, tmp)
            stream = {"label": "Film.2020.1080p.CZ.mkv", "detail": "4.2 GB | Zvuk: CZ 5.1", "source": "ws",
                      "url": "ws:x", "_direct": True, "_tracks": [{"lang": "CZ", "channels": "5.1", "codec": "AC3"}]}
            popis = engine._describe(stream, 0)
            self.assertEqual(popis["audio"], [{"lang": "CZ", "channels": "5.1", "codec": "AC3"}])
            self.assertEqual(popis["channels"], {"CZ": "5.1"})



class TestCeskyNazevZWikidat(unittest.TestCase):
    """Bez Luny/TMDB je název anglický a české soubory by přísný filtr zahodil."""

    def test_cesky_soubor_projde_s_anglickym_nazvem(self):
        from nokturno_core import engine as modul
        puvodni_wd, puvodni_cm = modul.local_titles, modul._cinemeta
        modul.local_titles = lambda imdb: ["Harry Potter a Ohnivý pohár", "Harry Potter a Ohnivá čaša"]
        modul._cinemeta = lambda ctype, imdb: {"name": "Harry Potter and the Goblet of Fire"}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                engine = Engine({}, tmp)
                meta = {"id": "tt0330373", "name": "Harry Potter and the Goblet of Fire", "year": 2005}
                self.assertIn("Harry Potter a Ohnivý pohár", engine.original_titles(meta, "movie"))
                _dotazy, relevant = engine._title_queries(meta, None, "movie", None, True)
                self.assertTrue(relevant("Harry Potter a Ohnivý pohár 2005 CZ dabing HD"))
                self.assertTrue(relevant("Harry.Potter.and.the.Goblet.of.Fire.2005.1080p"))
                self.assertFalse(relevant("Harry Potter a Fénixův řád 2007 CZ dabing"))
        finally:
            modul.local_titles, modul._cinemeta = puvodni_wd, puvodni_cm

    def test_vypadek_wikidat_se_necachuje(self):
        from nokturno_core import engine as modul
        from nokturno_core.lib.wikidata_api import WikidataError
        volani = []
        puvodni_wd, puvodni_cm = modul.local_titles, modul._cinemeta

        def spadne(imdb):
            volani.append(imdb)
            raise WikidataError("timeout")
        modul.local_titles, modul._cinemeta = spadne, (lambda ctype, imdb: {"name": ""})
        try:
            with tempfile.TemporaryDirectory() as tmp:
                engine = Engine({}, tmp)
                meta = {"id": "tt1", "name": "Film"}
                engine.original_titles(meta, "movie")
                engine.original_titles(meta, "movie")
                self.assertEqual(len(volani), 2, "po výpadku se má zkusit znovu")
        finally:
            modul.local_titles, modul._cinemeta = puvodni_wd, puvodni_cm



class TestKoncovkaNazvu(unittest.TestCase):
    def test_orizne_znak_z_ciziho_pisma(self):
        from nokturno_core.lib.streams import clean_file_name
        for dirty in ("Harry Potter A Ohnivý Pohár 2005 UHD CZ  ᚠ", "Harry Potter A Ohnivý Pohár 2005 UHD CZ (ሐ)",
                      "Harry Potter A Ohnivý Pohár 2005 UHD CZ  ก", "Harry Potter A Ohnivý Pohár 2005 UHD CZ (ア)",
                      "Harry Potter A Ohnivý Pohár 2005 UHD CZ  (Ⲁ)", "Harry Potter A Ohnivý Pohár 2005 UHD CZ (α)"):
            self.assertEqual(clean_file_name(dirty), "Harry Potter A Ohnivý Pohár 2005 UHD CZ", dirty)

    def test_skutecny_nazev_necha(self):
        from nokturno_core.lib.streams import clean_file_name
        for clean in ("Harry Potter A Ohnivý Pohár 2005 UHD CZ", "Matrix (1999) CZ", "Film 2020 CZ (HDR10)",
                      "Pelíšky 1999", "Pí (1998)", "Жмурки 2005", "Toy Story 5 (2026)", "Film CZ (ByDJ)", "Seriál S01E02 ž"):
            self.assertEqual(clean_file_name(clean), clean, clean)


class TestSkrytyDalsiDil(unittest.TestCase):
    """Odebraný „Další díl“ se musí přenést i na Kodi, které bylo vypnuté."""

    def test_synchronizace_skryteho_dilu(self):
        from nokturno_core.lib.store import Store
        from nokturno_core.lib.sync import apply_changes, collect_changes
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            obyvak, office = Store(a), Store(b)
            office.hide_next("tt1067851", "tt1067851:1:2")
            zmeny = collect_changes(office, 0)
            self.assertEqual(zmeny["next_hidden"]["tt1067851"]["ep"], "tt1067851:1:2")
            self.assertEqual(apply_changes(obyvak, zmeny), 1)
            self.assertEqual(obyvak.next_hidden("tt1067851"), "tt1067851:1:2")
            # starší záznam z druhé strany nepřepíše novější
            stary = {"next_hidden": {"tt1067851": {"ep": "tt1067851:1:1", "ts": 1}}}
            self.assertEqual(apply_changes(obyvak, stary), 0)
            self.assertEqual(obyvak.next_hidden("tt1067851"), "tt1067851:1:2")

    def test_stary_format_bez_casu(self):
        from nokturno_core.lib.store import Store
        from nokturno_core.lib.sync import collect_changes
        with tempfile.TemporaryDirectory() as a:
            s = Store(a)
            s.save("next_hidden", {"tt1": "tt1:1:2"})
            self.assertEqual(s.next_hidden("tt1"), "tt1:1:2")
            self.assertEqual(collect_changes(s, 0)["next_hidden"], {})


class TestTmdbKatalogy(unittest.TestCase):
    """Sjednocené menu Filmy/Seriály počítá s katalogy trendů a roku z TMDB."""

    def test_trendy_a_rok_bez_site(self):
        from nokturno_core.lib.tmdb_api import TmdbApi
        volani = []

        class Api(TmdbApi):
            def _get(self, path, **params):
                volani.append((path, params))
                if path.startswith("/genre/"):
                    return {"genres": [{"id": 35, "name": "Komedie"}]}
                return {"results": []}

        api = Api("klic")
        ids = [c["id"] for c in api.catalogs("movie")]
        self.assertEqual(ids, ["popular", "top_rated", "trending", "year"])
        rok = next(c for c in api.catalogs("series") if c["id"] == "year")
        self.assertTrue(rok["genre_required"])
        self.assertEqual(rok["genres"][-1], "1920")
        api.catalog("movie", "trending")
        api.catalog("series", "year", genre="1996")
        self.assertIn(("/trending/movie/week", {"page": 1}), volani)
        self.assertTrue(any(p == "/discover/tv" and q.get("first_air_date_year") == "1996" for p, q in volani))



class TestSosacBezOdkazu(unittest.TestCase):
    """Čerstvě přidané filmy mají v exportu „l": null — prázdné id nešlo otevřít."""

    def test_film_bez_odkazu_dostane_imdb_id(self):
        from nokturno_core.lib.sosac_direct import SosacDirect
        d = SosacDirect()
        m = d.movie_meta({"n": {"cs": "Chci tě!"}, "y": "2026", "m": "32332915", "l": None})
        self.assertEqual(m["id"], "tt32332915")
        self.assertEqual(m["imdb_id"], "tt32332915")

    def test_film_bez_odkazu_i_imdb_vypadne(self):
        from nokturno_core.lib.sosac_direct import SosacDirect
        self.assertIsNone(SosacDirect().movie_meta({"n": {"cs": "Nic"}, "l": None}))

    def test_film_s_odkazem_zustava(self):
        from nokturno_core.lib.sosac_direct import SosacDirect
        m = SosacDirect().movie_meta({"n": {"cs": "Harry"}, "m": "0241527", "l": "35da"})
        self.assertEqual(m["id"], "sosacd_m_35da")

    def test_katalog_prevadi_jen_stranku(self):
        from nokturno_core.lib.sosac_direct import SosacDirect
        d = SosacDirect()
        raw = [{"n": {"cs": f"F{i}"}, "l": f"h{i}"} for i in range(3000)]
        raw[1]["l"] = None
        calls = []
        orig = d.movie_meta
        d.movie_meta = lambda v: calls.append(1) or orig(v)
        d._get = lambda url, ttl=None: raw
        page = d.catalog("movie", "az", genre="D", skip=100, page=100)
        self.assertEqual(len(page), 100)
        self.assertEqual(page[0]["id"], "sosacd_m_h101")
        self.assertLess(len(calls), 250)


class TestSosacNovePridane(unittest.TestCase):
    """„Nově přidané" se dělí na s CZ dabingem a jen s CZ titulky, bez překryvu."""

    def test_rozdeleni_podle_jazyka(self):
        from nokturno_core.lib.sosac_direct import SosacDirect
        data = [
            {"n": {"cs": "Dabing"}, "m": "1", "l": "a", "d": ["cs"], "s": ["cs"]},
            {"n": {"cs": "Titulky"}, "m": "2", "l": "b", "d": ["en"], "s": ["cs"]},
            {"n": {"cs": "Nic"}, "m": "3", "l": "c", "d": ["ja"]},
        ]
        d = SosacDirect()
        d._get = lambda url, ttl=None: data
        self.assertEqual([m["name"] for m in d.catalog("movie", "moviesrecentlyadded_dub")], ["Dabing"])
        self.assertEqual([m["name"] for m in d.catalog("movie", "moviesrecentlyadded_subs")], ["Titulky"])



if __name__ == "__main__":
    unittest.main()
