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

from nokturno_core import Engine, NokturnoError, split_episode_id     # noqa: E402
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
                              "hellspy": False, "sledujteto": False, "fastshare": False, "prehrajto": False,
                              "cztor": False, "storage": False, "torrent": False})

    def test_rozpad_id_epizody(self):
        self.assertEqual(split_episode_id("tt0903747:2:5"), ("tt0903747", 2, 5))


class TestCoVyzadujeKodi(unittest.TestCase):
    """`default.py` a `service.py` doplňku importují lib ploše a jmenovitě."""

    IMPORTY = {
        "luna_api": ("LunaApi", "LunaError", "parse_base_url", "parse_token"),
        "cinemeta_api": ("CinemetaApi", "CinemetaError"),
        "tmdb_api": ("TmdbApi", "TmdbError"),
        "dash_api": ("DashApi", "DashApiError"),
        "sosac_api": ("SosacError", "is_sosac_id", "names_match"),
        "sosac_direct": ("EXPORT", "SosacDirect", "is_direct_id"),
        "enrich": ("enrich", "enrich_one", "shutdown_pool"),
        "hellspy_api": ("HellspyApi", "HellspyError"),
        "sledujteto_api": ("SledujtetoApi", "SledujtetoError"),
        "fastshare_api": ("FastshareApi", "FastshareError"),
        "mediainfo": ("describe", "probe", "quality_from_size"),
        "store": ("Store", "migrate_profile"),
        "sync": ("sync_once",),
        "streams": ("arrange", "estimate_rank", "langs_from_name", "parse_stream", "subs_from_name"),
        "trakt_api": ("TraktApi", "TraktError"),
        "webshare_api": ("SORTS", "WebshareApi", "WebshareError", "human_size"),
        "stats": ("COLLECT_URL", "Stats"),
        "abort": ("Aborted",),
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

    def test_madarstina_z_nazvu_souboru(self):
        self.assertEqual(streams.langs_from_name("film_hu_1080p"), {"HU"})
        self.assertEqual(streams.langs_from_name("film_magyar_1080p"), {"HU"})
        self.assertEqual(streams.subs_from_name("film_hu_tit_1080p"), {"HU"})
        self.assertEqual(streams.subs_from_name("film.HUtit.1080p"), {"HU"})


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

    def test_zprava_v_odpovedi_se_zachyti_a_msg_seen_se_posle_priste(self):
        """Server vrátí `message` — `send()` ji uloží do `last_message`, klient po
        zobrazení zavolá `mark_message_seen()` a příští hlášení už nese `msg_seen`."""
        class Resp:
            def __init__(self, body):
                self.body = body

            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n=-1): return self.body
            def getcode(self): return 200

        import urllib.request
        from unittest import mock
        body = json.dumps({"ok": True, "message": {"id": 5, "text": "Nová verze!"}}).encode("utf-8")
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None: Resp(body)):
            ok, why = self.stats.send("https://x/collect", version="3.1.9", product="kodi", ping=True)
        self.assertEqual((ok, why), (True, ""))
        self.assertEqual(self.stats.last_message, {"id": 5, "text": "Nová verze!"})

        self.stats.mark_message_seen(5)
        sent = {}
        with mock.patch.object(urllib.request, "urlopen",
                               lambda req, timeout=None: (sent.update(json.loads(req.data)), Resp(b"{}"))[1]):
            self.stats.send("https://x/collect", version="3.1.9", product="kodi", ping=True)
        self.assertEqual(sent["msg_seen"], 5)
        self.assertIsNone(self.stats.last_message, "prázdná odpověď = žádná (nová) zpráva")

    def test_stara_odpoved_bez_message_nerozbije_odeslani(self):
        """Odpověď bez `message` (starší chování serveru, nebo prázdné tělo) nesmí spadnout."""
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self, n=-1): return b'{"ok": true}'
            def getcode(self): return 200

        import urllib.request
        from unittest import mock
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None: Resp()):
            ok, why = self.stats.send("https://x/collect", version="3.1.9", product="kodi", ping=True)
        self.assertEqual((ok, why), (True, ""))
        self.assertIsNone(self.stats.last_message)


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

    def test_radek_luny_slouci_kopii_z_jejiho_hledani_i_primy_nalez(self):
        """Extraktoři 2x06: tentýž soubor přišel jako Lunin řádek, kopie z Lunina
        hledání (bez názvu) a přímý nález z WebShare — má zůstat jeden řádek Luny
        (nese jazyky) s přibaleným přímým odkazem. Jiný soubor podobné velikosti zůstane."""
        luna = {"source": "main", "label": "(WS) Full HD", "detail": "2.4 GB | Zvuk: CZ 2.0", "url": "l"}
        search = {"source": "search", "label": "(WS) Full HD", "detail": "2.4 GB", "url": "s"}
        ws = {"source": "ws", "label": "Extraktori.2x06.1080p.mkv", "detail": "2.5 GB", "url": "ws:1", "_direct": True}
        daleko = {"source": "ws", "label": "Extraktori.2x06.jiny.1080p.mkv", "detail": "3.0 GB", "url": "ws:2",
                  "_direct": True}
        rows = [luna, search, ws, daleko]
        for s in rows:
            streams.parse_stream(s)
        out = Engine._merge_direct(rows)
        self.assertEqual([s["url"] for s in out], ["l", "ws:2"])
        self.assertEqual(out[0]["_ws_url"], "ws:1")
        # bez Luny se přímé nálezy nesrovnávají na nic a zůstávají všechny různé
        rows = [{"source": "ws", "label": "a.mkv", "detail": "1 GB", "url": "1", "_direct": True},
                {"source": "hs", "label": "b.mkv", "detail": "1 GB", "url": "2", "_direct": True}]
        for s in rows:
            streams.parse_stream(s)
        self.assertEqual(len(Engine._merge_direct(rows)), 2)

    def test_lunin_radek_spojeny_s_webshare_dostane_hlavicku_souboru(self):
        """Řádek z Luny spárovaný s nálezem z WebShare (`_ws_url`) má titulky a rozlišení
        ze souboru — dřív se četl jen `url` (http Luny), takže je řádek neměl vůbec."""
        info = {"width": 3840, "height": 1920, "duration": 3772, "size": 5184894213,
                "audio": [{"lang": "EN", "channels": "5.1", "codec": "EAC3"}],
                "subs": ["EN", "CZ", "FR"]}
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({}, tmp)
            cteno = []
            engine._media_from_file = lambda url: (cteno.append(url), info)[1]
            luna = {"url": "http://luna:7126/stream/x", "label": "(WS) 4K", "detail": "5.0 GB",
                    "source": "main", "_tracks": [{"lang": "EN", "channels": "5.1"}], "_ws_url": "ws:abc"}
            engine._fill_audio([luna])
            self.assertEqual(cteno, ["http://luna:7126/stream/x"], "čte se Lunin vlastní odkaz, ne odhadem spárovaný soubor")
            self.assertEqual(set(luna["subs"]), {"CZ", "EN", "FR"})
            self.assertEqual(luna["_media"]["width"], 3840)
            info["audio"] = [{"lang": "", "channels": "5.1", "codec": "EAC3"}]
            luna2 = dict(luna, _tracks=[{"lang": "EN", "channels": "5.1"}], _media=None)
            luna2.pop("_media")
            engine._fill_audio([luna2])
            self.assertEqual(luna2["_tracks"][0]["lang"], "EN", "jazyk od Luny se souborem bez jazyka nepřepíše")

    def test_volby_kodi_audio_probe_fresh_a_uvolneny_filtr(self):
        """Volby, které si doplněk pro Kodi bere z nastavení: limit čtení hlaviček,
        zahřívání bez čtení cache a uvolněný fulltext značený `_loose` mimo cache."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"audio_probe": "0"}, tmp)
            rows = [{"url": "ws:1", "label": "a", "detail": "1 GB"}]
            counts = []
            self.assertEqual(engine._fill_audio(rows, on_count=counts.append), rows)
            self.assertEqual(counts, [0], "s vypnutým čtením hlaviček se nic nečte a průběh o tom ví")
            engine = Engine({"audio_probe": "abc"}, tmp)
            engine._media_from_file = lambda url: {}
            engine._fill_audio(rows, on_count=counts.append)
            self.assertEqual(counts[-1], 1, "nesmysl v nastavení = výchozí limit")

            # on_audio_progress: kolik hlaviček je hotovo z kolika se doopravdy čte
            engine = Engine({}, tmp)
            engine._media_from_file = lambda url: {}
            audio_progress = []
            two_rows = [{"url": "ws:1", "label": "a", "detail": "1 GB"},
                        {"url": "ws:2", "label": "b", "detail": "1 GB"}]
            engine._fill_audio(two_rows, on_audio_progress=lambda done, total: audio_progress.append((done, total)))
            self.assertEqual(audio_progress[0], (0, 2), "nejdřív se pošle skutečný total, ne odhad")
            self.assertEqual(sorted(audio_progress[1:]), [(1, 2), (2, 2)])

            # fresh: cache streamů se jen zapíše, nečte
            calls = []
            engine = Engine({"fresh": True}, tmp)
            engine.store.cached_if("k", 3600, lambda: {"stara": 1})
            self.assertEqual(engine.store.cached_if("k", 3600, lambda: calls.append(1) or {"nova": 1}, fresh=True),
                             {"nova": 1})
            self.assertEqual(engine.store.cached_if("k", 3600, lambda: {"treti": 1}), {"nova": 1})

            # strict=False: zdroje s uvolněným filtrem, řádky `_loose`, žádná cache
            engine = Engine({}, tmp)
            engine.meta = lambda *a, **k: ({"name": "Film", "year": 2020}, None)
            engine.api_for = lambda item_id: (_ for _ in ()).throw(NokturnoError("není nastaven"))
            seen = {}
            def ws_streams(meta, video=None, ctype="movie", alt=None, strict=True, failures=None):
                seen["strict"] = strict
                return [{"url": "ws:1", "label": "Film.2020.mkv", "detail": "1.2 GB", "source": "ws", "_direct": True}]
            engine._webshare_streams = ws_streams
            engine._webshare_subtitles = lambda *a, **k: []
            engine._fill_audio = lambda rows, *a, **k: rows
            out = engine.raw_streams("movie", "tt1", strict=False)
            self.assertFalse(seen["strict"])
            self.assertTrue(out and out[0]["_loose"])
            self.assertIsNone(engine.store.cached_if(engine._streams_cache_key("movie", "tt1"), 3600, lambda: None),
                              "uvolněný výsledek se necachuje")
            out = engine.raw_streams("movie", "tt1")
            self.assertTrue(seen["strict"])
            self.assertFalse(out[0].get("_loose"))

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



def _zapisovac(tmp, znacka, kolik):
    """Samostatný proces: připíše `kolik` klíčů do watched.json. Běží v testu níž."""
    from nokturno_core.lib.store import Store
    store = Store(tmp)
    for i in range(kolik):
        store.set_resume(f"{znacka}{i}", 10 + i, 1000)
        time.sleep(0.002)


class TestStoreZamekMeziProcesy(unittest.TestCase):
    """Načti–uprav–ulož nad sdíleným JSON (audit 2026-09-19, nález 27).

    `os.replace` je atomický, celý cyklus ne: prohrávající zápis tiše zahodil, co mezitím
    uložil jiný proces. Kodi je na ty soubory víc procesů najednou (plugin při každém
    kliknutí, služba na pozadí každých 30 s při přehrávání)."""

    def setUp(self):
        from nokturno_core.lib.store import Store
        self.tmp = tempfile.mkdtemp()
        self.store = Store(self.tmp)

    def test_soubezne_procesy_neztrati_zapis(self):
        import multiprocessing
        ctx = multiprocessing.get_context("fork" if sys.platform != "win32" else "spawn")
        procesy = [ctx.Process(target=_zapisovac, args=(self.tmp, znacka, 12))
                   for znacka in ("a", "b", "c")]
        for proces in procesy:
            proces.start()
        for proces in procesy:
            proces.join(60)
        from nokturno_core.lib.store import Store
        data = Store(self.tmp).load("watched", {})
        chybi = [f"{znacka}{i}" for znacka in ("a", "b", "c") for i in range(12)
                 if f"{znacka}{i}" not in data]
        self.assertEqual(chybi, [], f"ztracené zápisy: {len(chybi)} z 36")

    def test_updating_cte_cerstva_data_a_uklada(self):
        self.store.save("watched", {"stary": {"playcount": 1}})
        jiny = self.store.__class__(self.tmp)
        jiny.set_watched("mezitim")
        with self.store.updating("watched", {}) as data:
            self.assertIn("mezitim", data, "transakce musí číst z disku, ne z paměti")
            data["novy"] = {"playcount": 1}
        self.assertEqual(set(jiny.load("watched", {})), {"stary", "mezitim", "novy"})

    def test_zamek_se_opravdu_bere_a_zase_pousti(self):
        from nokturno_core.lib import store as store_mod
        if store_mod.fcntl is None:
            self.skipTest("bez fcntl (Windows)")
        volani = []
        puvodni = store_mod.fcntl.flock
        store_mod.fcntl.flock = lambda fd, op: volani.append(op) or puvodni(fd, op)
        try:
            self.store.set_resume("tt1", 10, 100)
        finally:
            store_mod.fcntl.flock = puvodni
        self.assertEqual(volani, [store_mod.fcntl.LOCK_EX, store_mod.fcntl.LOCK_UN])
        self.assertTrue(pathlib.Path(self.tmp, "watched.lock").exists())

    def test_vnorena_transakce_nezamrzne(self):
        """`toggle_favourite` volá uvnitř `remember_item` — zámek patří popisovači,
        takže druhé otevření téhož souboru by čekalo samo na sebe. Vnořená transakce
        nad týmž jménem navíc musí dostat týž objekt, jinak by vnější uložení přepsalo
        to, co zapsala vnitřní."""
        self.store.toggle_favourite("tt1", {"title": "Film"})
        with self.store.updating("items", {}) as data:
            self.assertIn("tt1", data)
            with self.store.updating("items", {}) as znovu:
                znovu["tt2"] = {"title": "Druhý"}
        self.assertEqual(set(self.store.load("items", {})), {"tt1", "tt2"})

    def test_nezamykatelny_soubor_zapis_nezastavi(self):
        """Síťový disk bez zámků, Android SAF — radši bez zámku než spadnout."""
        from nokturno_core.lib import store as store_mod
        if store_mod.fcntl is None:
            self.skipTest("bez fcntl (Windows)")
        puvodni = store_mod.fcntl.flock
        store_mod.fcntl.flock = lambda fd, op: (_ for _ in ()).throw(OSError("nepodporováno"))
        try:
            self.store.set_resume("tt9", 5, 50)
        finally:
            store_mod.fcntl.flock = puvodni
        self.assertEqual(self.store.resume("tt9"), (5, 50))

    def test_soubory_s_tokeny_nejsou_citelne_pro_ostatni(self):
        if sys.platform == "win32":
            self.skipTest("práva jen na POSIX")
        self.store.set_trakt({"access_token": "tajne"})
        prava = pathlib.Path(self.tmp, "trakt.json").stat().st_mode & 0o777
        self.assertEqual(prava, 0o600, f"{prava:o}")


class TestResumeStream(unittest.TestCase):
    """Pokračování ve sledování si k pozici pamatuje i vnitřní referenci streamu
    (`ws:…`/`hs:…:…`/…), aby přehrání nemuselo znovu prohledávat všechny zdroje."""

    def setUp(self):
        from nokturno_core.lib.store import Store
        self.store = Store(tempfile.mkdtemp())

    def test_bez_streamu_vraci_none(self):
        self.store.set_resume("film1", 100, 1000)
        self.assertIsNone(self.store.resume_stream("film1"))

    def test_ulozi_a_vrati_stream(self):
        self.store.set_resume("film1", 100, 1000, stream_url="ws:abc", stream_subs="cz.srt")
        self.assertEqual(self.store.resume_stream("film1"), ("ws:abc", "cz.srt"))

    def test_dalsi_zapis_pozice_bez_streamu_predchozi_referenci_zachova(self):
        """Cross-device sync ani ruční nastavení pozice stream neznají — nesmí smazat,
        co si tam dřív uložilo přehrávání (`Player.save_resume`)."""
        self.store.set_resume("film1", 100, 1000, stream_url="ws:abc", stream_subs="")
        self.store.set_resume("film1", 200, 1000)
        self.assertEqual(self.store.resume_stream("film1"), ("ws:abc", ""))


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

    def test_on_source_done_hlasi_odkud_a_kolik(self):
        """Ukazatel průběhu v Kodi má vědět nejen kolik procent, ale i odkud kolik
        streamů přišlo — `on_source_done(label, count)` se volá zvlášť za každý zdroj,
        i za ten, co zrovna vypadl (viz `_engine`: Luna hodí chybu, nahlásí se 0)."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            hlaseno = []
            engine.raw_streams("movie", "tt1", on_source_done=lambda label, n: hlaseno.append((label, n)))
            self.assertIn(("Luna", 0), hlaseno, "primární zdroj i po chybě nahlásí 0, ne že by chyběl")
            self.assertIn(("WebShare", 1), hlaseno)
            self.assertIn(("HellSpy", 0), hlaseno)

    def test_probe_audio_false_vynecha_hlavicky_a_cache(self):
        """Kodi hromadná klasifikace dabing/titulky (2026-09-15) volá `raw_streams` s
        `probe_audio=False` — desítky titulů, čtení hlaviček by bylo neúnosně pomalé.
        Nesmí ani spadnout do 72h cache streamů, jinak by na 72 h zablokoval opravdové
        ověření hlaviček ve skutečném dialogu streamů pro tentýž titul."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            volani = []
            puvodni = engine._fill_audio
            engine._fill_audio = lambda *a, **k: (volani.append(1), puvodni(*a, **k))[1]
            found = engine.raw_streams("movie", "tt1", probe_audio=False)
            self.assertEqual(len(found), 1, "stream z WebShare musí zůstat i bez čtení hlaviček")
            self.assertEqual(volani, [], "_fill_audio se nesmí zavolat")
            cache = list(pathlib.Path(tmp, "cache").glob("*.json"))
            self.assertEqual(cache, [], "lehčí výsledek se nesmí zapsat do 72h cache streamů")

    def test_probe_audio_false_vynecha_i_vlastni_uloziste(self):
        """Nedostupné vlastní úložiště (NAS/DAV) se bez `probe_audio=False` zkoušelo
        u každého kandidáta znovu (mimo 72h cache streamů, viz `_storage_streams`) —
        na Office 2026-09-15 to s nedostupným NAS dusilo klasifikaci dabing/titulky
        na desítky sekund na kandidáta."""
        from nokturno_core.lib.storage_api import StorageError

        class MrtveUloziste:
            name = "NAS"

            def files(self):
                raise StorageError("Network is unreachable")

        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            engine._storages = [MrtveUloziste()]
            failures = []
            found = engine.raw_streams("movie", "tt1", probe_audio=False, failures=failures)
            self.assertEqual(len(found), 1, "stream z WebShare musí zůstat")
            self.assertNotIn("NAS", [label for label, _e in failures], "vlastní úložiště se nesmí ani zkusit")



class TestAudit20260919(unittest.TestCase):
    """Nálezy auditu jádra 2026-09-19 (viz Nokturno/AUDIT-2026-09-19.md)."""

    def _engine(self, tmp, **options):
        engine = Engine(options, tmp)
        engine.api_for = lambda item_id: (_ for _ in ()).throw(NokturnoError("není nastaven"))
        engine.meta = lambda ctype, item_id, series_id=None: ({"id": item_id, "name": "Film", "year": 2020}, None)
        engine._cross_streams = lambda *a, **k: []
        engine._fill_audio = lambda streams, *a, **k: streams
        engine._hellspy_streams = lambda *a, **k: []
        engine._webshare_subtitles = lambda *a, **k: []
        engine._webshare_streams = lambda *a, **k: [
            {"url": "ws:abc", "label": "Film.2020.1080p.CZ.mkv", "detail": "4.2 GB", "source": "ws", "_direct": True}]
        # bez sítě: český název z Wikidat a anglický z Cinemety leží mimo deadline zdrojů
        # a na CI běžci s pomalou cestou k nim časové testy padaly (2026-09-21, 5–12 s)
        engine.original_titles = lambda *a, **k: []
        engine._with_local_title = lambda ctype, base_id, meta: meta
        return engine

    def test_selhany_login_webshare_je_vypadek(self):
        """Seznam bez hlavního zdroje se dřív uložil na 72 h — `self.ws` vrátilo None a
        `_webshare_streams` tiše prázdno, nic ve `failures`."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"ws_username": "u", "ws_password": "p"}, tmp)
            engine._ws_ready, engine._ws = False, None
            engine._ws_retry_after = time.time() + 3600
            engine.ws_error = webshare_api.WebshareError("síť")
            failures = []
            self.assertEqual(engine._webshare_streams({"name": "Film"}, failures=failures), [])
            self.assertEqual([l for l, _e in failures], ["WebShare"])
            bez = Engine({}, tmp)
            failures = []
            self.assertEqual(bez._webshare_streams({"name": "Film"}, failures=failures), [])
            self.assertEqual(failures, [], "bez účtu není co hlásit")

    def test_klic_cache_streamu_nese_otisk_zdroju(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Engine({}, tmp)._streams_cache_key("movie", "tt1")
            b = Engine({"hs_enabled": True}, tmp)._streams_cache_key("movie", "tt1")
            c = Engine({"ws_username": "u"}, tmp)._streams_cache_key("movie", "tt1")
            self.assertEqual(len({a, b, c}), 3)
            self.assertEqual(a, Engine({}, tmp)._streams_cache_key("movie", "tt1"), "stejné nastavení = stejný klíč")
            self.assertTrue(a.startswith("streams7:movie:tt1:"))

    def test_titulky_jen_pri_probe_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            volani = []
            engine._webshare_subtitles = lambda *a, **k: volani.append(1) or []
            engine.raw_streams("movie", "tt1", probe_audio=False)
            self.assertEqual(volani, [], "hromadná klasifikace titulky nepoužije")
            engine.raw_streams("movie", "tt1")
            self.assertEqual(volani, [1])

    def test_pomaly_zdroj_neblokuje_ostatni(self):
        """`kolo()` čekalo na nejpomalejší zdroj bez stropu; teď `SOURCE_DEADLINE` a opozdilec
        je výpadek (výsledek se necachuje), ostatní streamy přijdou hned."""
        from nokturno_core import engine as mod
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            engine._hellspy_streams = lambda *a, **k: time.sleep(2) or []
            puvodni = mod.SOURCE_DEADLINE
            mod.SOURCE_DEADLINE = 0.3
            try:
                failures = []
                t = time.monotonic()
                found = engine.streams("movie", "tt1", failures=failures)
            finally:
                mod.SOURCE_DEADLINE = puvodni
            self.assertLess(time.monotonic() - t, 1.5)
            self.assertEqual(len(found), 1)
            self.assertEqual([l for l, _e in failures], ["HellSpy"])
            self.assertIn("neodpověděl", str(failures[0][1]))

    def test_druhe_kolo_nedostane_novy_strop(self):
        """`raw_streams()` volá `kolo()` podruhé, když hlavní zdroj nic nenašel a český
        název se liší. Rozpočet `SOURCE_DEADLINE` je společný pro obě kola — jinak se
        stropy sečtou a hledání trvá dvojnásobek (na produkci naměřeno až 31,4 s)."""
        from nokturno_core import engine as mod
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            engine._webshare_streams = lambda *a, **k: time.sleep(5) or []
            engine._with_local_title = lambda ctype, base_id, meta: dict(meta, name="Český název")
            puvodni = (mod.SOURCE_DEADLINE, mod.MIN_ROUND_DEADLINE)
            mod.SOURCE_DEADLINE, mod.MIN_ROUND_DEADLINE = 1.0, 0.2
            try:
                t = time.monotonic()
                engine.raw_streams("movie", "tt1", probe_audio=False)
                trvalo = time.monotonic() - t
            finally:
                mod.SOURCE_DEADLINE, mod.MIN_ROUND_DEADLINE = puvodni
            self.assertTrue(engine.last_timings.get("znovu česky"), "druhé kolo se musí spustit")
            self.assertLess(trvalo, 1.7, "obě kola dohromady nesmí přesáhnout rozpočet plus minimum")

    def test_prvni_kolo_ma_cely_rozpocet(self):
        """Zkrácení platí jen na další kola — první nesmí přijít o čas kvůli tomu,
        co běželo před ním."""
        from nokturno_core import engine as mod
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            engine._hellspy_streams = lambda *a, **k: time.sleep(0.6) or [
                {"url": "hs:1:a", "label": "Film.2020.720p.mkv", "detail": "1 GB", "source": "hs", "_direct": True}]
            puvodni = mod.SOURCE_DEADLINE
            mod.SOURCE_DEADLINE = 1.0
            try:
                failures = []
                found = engine.raw_streams("movie", "tt1", probe_audio=False, failures=failures)
            finally:
                mod.SOURCE_DEADLINE = puvodni
            self.assertEqual(failures, [], "pomalejší zdroj se do rozpočtu vejde")
            self.assertEqual(len(found), 2)

    def test_hs_odkaz_s_nesmyslem_se_odmitne(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({}, tmp)
            for zly in ("hs:../x:abc", "hs:12?x=1:abc", "hs:12:ab/cd", "hs::"):
                with self.assertRaises(NokturnoError, msg=zly):
                    engine.resolve(zly)

    def test_dotazy_jen_z_prvnich_variant_nazvu(self):
        from nokturno_core import engine as mod
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({}, tmp)
            engine.original_titles = lambda *a, **k: [f"Orig{i}" for i in range(8)]
            queries, _relevant = engine._title_queries({"name": "Film", "year": 2020})
            self.assertEqual(len(queries), 2 + mod.MAX_TITLE_VARIANTS)


class TestSoubezneHledani(unittest.TestCase):
    """Hlavní zdroj (Luna/Sosáč) se dřív čekal předem — studená Luna ~6 s, než se vůbec
    začalo hledat jinde (Office 2026-09-18). Teď běží souběžně s ostatními i s titulky."""

    def _engine(self, tmp, luna_streams, local_title=None):
        from nokturno_core.lib.luna_api import LunaApi
        engine = Engine({}, tmp)
        self.starty, self.dotazy = {}, []

        class PomalaLuna(LunaApi):
            def __init__(self):
                pass

            def streams(inner, *a, **k):
                self.starty["Luna"] = time.monotonic()
                time.sleep(0.4)
                return list(luna_streams)

        def ws(meta, *a, **k):
            self.starty.setdefault("WebShare", time.monotonic())
            self.dotazy.append(meta.get("name"))
            return [{"url": "ws:" + meta.get("name"), "label": "Film.2020.1080p.CZ.mkv", "detail": "4.2 GB",
                     "source": "ws", "_direct": True}]
        engine.api_for = lambda item_id: PomalaLuna()
        engine.meta = lambda ctype, item_id, series_id=None: ({"id": item_id, "name": "Sunday League", "year": 2020}, None)
        engine._cross_streams = lambda *a, **k: []
        engine._hellspy_streams = lambda *a, **k: []
        engine._webshare_streams = ws
        engine._webshare_subtitles = lambda *a, **k: [{"url": "ws:sub", "lang": "cs"}]
        engine._fill_audio = lambda streams, *a, **k: streams
        engine._storage_streams = lambda *a, **k: []
        engine._with_local_title = lambda ctype, base_id, meta: (
            {**meta, "name": local_title, "_orig": meta["name"]} if local_title else meta)
        return engine

    def test_hlavni_zdroj_neblokuje_ostatni(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp, [{"url": "http://luna/x", "title": "Film 1080p", "name": "Luna"}])
            found = engine.raw_streams("movie", "tt1")
            self.assertLess(self.starty["WebShare"] - self.starty["Luna"], 0.3, "WebShare nesmí čekat na Lunu")
            self.assertEqual(self.dotazy, ["Sunday League"], "Luna něco našla — český název se nehledá")
            self.assertTrue(all(st.get("subtitles") for st in found if st.get("source") == "ws"),
                            "titulky z téže souběžné dávky se přiřadí")
            self.assertIn("Luna", engine.last_timings["zdroje"])

    def test_prazdny_hlavni_zdroj_hleda_znovu_cesky(self):
        """Stejný výsledek jako dřív: bez streamů z Luny hledají ostatní zdroje s českým názvem."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp, [], local_title="Okresní přebor")
            found = engine.raw_streams("movie", "tt1")
            self.assertEqual(self.dotazy, ["Sunday League", "Okresní přebor"])
            self.assertEqual([st["url"] for st in found], ["ws:Okresní přebor"], "platí výsledek s českým názvem")
            self.assertTrue(engine.last_timings.get("znovu česky"))

    def test_prazdny_hlavni_zdroj_bez_jineho_nazvu_nehleda_dvakrat(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp, [])
            engine.raw_streams("movie", "tt1")
            self.assertEqual(self.dotazy, ["Sunday League"])

    def test_chyba_titulku_neni_vypadek_zdroje(self):
        """Výsledek s výpadkem se necachuje — kvůli titulkům by se streamy hledaly pořád znovu."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp, [{"url": "http://luna/x", "title": "Film 1080p", "name": "Luna"}])
            engine._webshare_subtitles = lambda *a, **k: 1 / 0
            failures, hlaseno = [], []
            found = engine.raw_streams("movie", "tt1", failures=failures,
                                       on_source_done=lambda label, n: hlaseno.append(label))
            self.assertEqual(failures, [])
            self.assertTrue(found)
            self.assertNotIn("Titulky", hlaseno, "titulky nejsou zdroj streamů")


class TestSlucovaniVerzi(unittest.TestCase):
    """Stejný film dvakrát na HellSpy, 18,6 a 18,6 GB, stejný zvuk i titulky — v dialogu dva
    totožné řádky (Office 2026-09-18, Pelíšky). Uživatel mezi takovými verzemi nevybírá,
    velikost ±10 % je mu jedno."""

    @staticmethod
    def st(url, label, gb, source="hs"):
        from nokturno_core.lib.streams import parse_stream
        return parse_stream({"url": url, "label": label, "detail": f"{gb} GB", "source": source, "_direct": True})

    def test_overeny_jazyk_z_hlavicky_neni_odhad(self):
        """Čertí brko: 1080p z FastShare („(CZ)“ v názvu, čeština potvrzená hlavičkou) bylo za
        720p i 480p ze Sosáče — příznak odhadu z názvu přežil přepočet po čtení hlavičky."""
        from nokturno_core.lib.streams import arrange, parse_stream
        fs = parse_stream({"url": "fs:1", "label": "Čertí brko (2018)(CZ).mp4", "detail": "4.1 GB",
                           "source": "fs", "_direct": True})
        self.assertTrue(fs.get("_langs_from_name"))
        fs["detail"] += " | Zvuk: CZ"          # tak to po hlavičce zapíše `_fill_audio`
        fs.pop("quality_rank")
        parse_stream(fs)
        fs["quality_rank"] = 3
        self.assertFalse(fs.get("_langs_from_name"))
        sosac = parse_stream({"url": "streamuj:1", "label": "Sosáč CZ - HD", "detail": "1.4 GB", "source": "sosac"})
        self.assertEqual([s["url"] for s in arrange([sosac, fs], pref_lang="CZ", order="quality")],
                         ["fs:1", "streamuj:1"])

    def test_slouci_stejne_verze_i_napric_zdroji(self):
        from nokturno_core.lib.streams import group_streams
        out = group_streams([self.st("hs:1", "Pelisky.1080p.CZ.mkv", 18.6), self.st("hs:2", "Pelisky.1080p.CZ.mkv", 18.6),
                             self.st("ws:3", "Pelisky 1080p CZ.mkv", 17.3, "ws")])
        self.assertEqual([s["url"] for s in out], ["hs:1"])
        self.assertEqual([s["url"] for s in out[0]["_alts"]], ["hs:2", "ws:3"])

    def test_nesloucuje_co_uzivatel_rozlisuje(self):
        from nokturno_core.lib.streams import group_streams
        out = group_streams([
            self.st("a", "Film.1080p.CZ.mkv", 18), self.st("b", "Film.2160p.CZ.mkv", 18),       # kvalita
            self.st("c", "Film.1080p.EN.mkv", 18), self.st("d", "Film.1080p.HDR.CZ.mkv", 18),   # jazyk, HDR
            self.st("e", "Film.1080p.CZ.mkv", 12), self.st("f", "Film.1080p.CZ.mkv", 18, "dav"),  # velikost, úložiště
        ])
        self.assertEqual(len(out), 6)
        self.assertFalse(any(s.get("_alts") for s in out))

    def test_hlavicky_jen_u_zastupcu_a_rozbaleni(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"merge_streams": True}, tmp)
            engine.api_for = lambda item_id: (_ for _ in ()).throw(NokturnoError_("není nastaven zdroj"))
            engine.meta = lambda *a, **k: ({"id": "tt1", "name": "Pelíšky", "year": 1999}, None)
            engine._cross_streams = lambda *a, **k: []
            engine._hellspy_streams = lambda *a, **k: [
                {"url": "hs:1", "label": "Pelisky.1999.1080p.CZ.mkv", "detail": "18.6 GB", "source": "hs", "_direct": True},
                {"url": "hs:2", "label": "Pelisky (1999) 1080p BluRay CZ.mkv", "detail": "18.6 GB", "source": "hs",
                 "_direct": True}]
            engine._webshare_streams = lambda *a, **k: []
            engine._webshare_subtitles = lambda *a, **k: []
            engine._storage_streams = lambda *a, **k: []
            cteno = []
            engine._media_from_file = lambda url: (cteno.append(url), {})[1]
            found = engine.raw_streams("movie", "tt1")
            self.assertEqual([s["url"] for s in found], ["hs:1"])
            self.assertEqual(cteno, ["hs:1"], "hlavička jen u zástupce skupiny")
            self.assertEqual(engine.last_timings["sloučeno"], 1)
            vse = engine.expand_streams(found, ({"name": "Pelíšky", "year": 1999}, None))
            self.assertEqual(sorted(s["url"] for s in vse), ["hs:1", "hs:2"])
            self.assertFalse(any(s.get("_alts") for s in vse))

    def test_bez_volby_se_neslucuje(self):
        """Stremio a HA mají jen jeden odkaz na řádek a nemají „Zobrazit všechny“."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({}, tmp)
            engine.api_for = lambda item_id: (_ for _ in ()).throw(NokturnoError_("není nastaven zdroj"))
            engine.meta = lambda *a, **k: ({"id": "tt1", "name": "Pelíšky", "year": 1999}, None)
            engine._cross_streams = engine._webshare_streams = lambda *a, **k: []
            engine._hellspy_streams = lambda *a, **k: [
                {"url": f"hs:{i}", "label": f"Pelisky.1999.1080p.CZ.v{i}.mkv", "detail": "18.6 GB", "source": "hs",
                 "_direct": True} for i in (1, 2)]
            engine._webshare_subtitles = engine._storage_streams = lambda *a, **k: []
            engine._media_from_file = lambda url: {}
            self.assertEqual(len(engine.raw_streams("movie", "tt1")), 2)

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



class TestFastshare(unittest.TestCase):
    """Klient FastShare bez sítě — odpovědi ve tvaru z `api_kodi.php` (ověřeno živě 2026-09-14)."""

    SOUBOR = {"id": "11331345", "filename": "Matrix 1 (1999) CZ dabing.mkv",
              "data": {"unit": "B", "value": "4207430497"},
              "download_url": "https://data4.fastshare.cloud/download.php?id=11331345",
              "resolution": "1920x800", "duration": {"unit": "s", "value": "8178"},
              "thumbnail": "https://img.fastshare.cloud/t.jpg"}

    def setUp(self):
        from nokturno_core.lib import fastshare_api as fs
        from nokturno_core.lib.store import Store
        self.fs = fs
        self.store = Store(tempfile.mkdtemp())
        self.calls = []
        self._orig = fs.urllib.request.urlopen

    def tearDown(self):
        self.fs.urllib.request.urlopen = self._orig

    def _server(self, handler):
        import io, urllib.error, urllib.parse
        test = self

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def urlopen(req, timeout=None):
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(req.full_url).query))
            test.calls.append(params)
            status, body = handler(params)
            data = json.dumps(body).encode()
            if status >= 400:
                raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(data))
            return Resp(data)
        self.fs.urllib.request.urlopen = urlopen

    def _login(self, credit="5000", unlimited="False"):
        return 200, {"user": {"hash": "H1", "unlimited": unlimited, "data": {"value": credit}}}

    def test_hledani_bez_prihlaseni(self):
        self._server(lambda p: (200, {"search": {"total": "2", "file": [
            self.SOUBOR, {**self.SOUBOR, "id": "9", "download_url": "https://jinde.cz/download.php?id=9"}]}}))
        files, total = self.fs.FastshareApi("u", "p", cache=self.store).search("matrix")
        self.assertEqual(total, 2)
        self.assertEqual(len(files), 1, "soubor z cizího serveru se nevrátí")
        f = files[0]
        self.assertEqual((f["id"], f["server"], f["size"], f["duration"]), ("11331345", "data4", 4207430497, 8178))
        self.assertEqual((f["media"]["width"], f["media"]["height"]), (1920, 800))
        self.assertEqual(self.fs.make_ref(f), "fs:11331345:data4:4207430497")
        self.assertNotIn("login", self.calls[0])
        self.fs.FastshareApi("u", "p").search("Pelíšky")
        self.assertEqual(self.calls[-1]["term"], "Pelisky", "FastShare diakritiku v dotazu nezvládá")

    def test_hledani_prezije_spatne_zakodovany_bajt_v_odpovedi(self):
        """FastShare umí poslat v názvu souboru bajt, co do UTF-8 nepatří (Office 2026-09-14,
        „The Matrix 1999" spadlo na pozici 12320 uprostřed výpisu) — nahradí se, ne pád."""
        import io
        spatne = (b'{"search": {"total": "1", "file": [{"id": "1", '
                 b'"filename": "Matrix \xed video.mkv", "data": {"unit": "B", "value": "1"}, '
                 b'"download_url": "https://data4.fastshare.cloud/download.php?id=1", '
                 b'"resolution": "0x0", "duration": {"unit": "s", "value": "0"}, "thumbnail": ""}]}}')

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        self.fs.urllib.request.urlopen = lambda req, timeout=None: Resp(spatne)
        files, total = self.fs.FastshareApi("u", "p", cache=self.store).search("matrix")
        self.assertEqual(total, 1)
        self.assertIn("Matrix", files[0]["name"])

    def test_odkaz_s_cookie_a_hash_se_pamatuje(self):
        self._server(lambda p: self._login())
        api = self.fs.FastshareApi("u", "tajne", cache=self.store)
        url, headers = api.request("fs:11331345:data4:1000")
        self.assertEqual(url, "https://data4.fastshare.cloud/download.php?id=11331345")
        self.assertEqual(headers["Cookie"], "FASTSHARE=H1")
        self.assertIn("|Cookie=FASTSHARE%3DH1", api.kodi_url("fs:11331345:data4:1000"))
        # druhý klient nad stejným úložištěm se znovu nepřihlašuje
        self.fs.FastshareApi("u", "tajne", cache=self.store).request("fs:1:data4:1000")
        self.assertEqual(sum(1 for c in self.calls if c.get("process") == "login"), 1)

    def test_malo_kreditu_srozumitelna_chyba(self):
        self._server(lambda p: self._login(credit="100"))
        with self.assertRaises(self.fs.FastshareError) as ctx:
            self.fs.FastshareApi("u", "p").request("fs:1:data4:4207430497")
        self.assertIn("kredit", str(ctx.exception))
        # neomezený tarif kredit nehlídá
        self._server(lambda p: self._login(credit="0", unlimited="True"))
        self.assertTrue(self.fs.FastshareApi("u", "p").request("fs:1:data4:4207430497")[0])

    def test_spatne_heslo_a_cizi_odkaz(self):
        self._server(lambda p: (401, {"response": "INVALID_LOGIN"}))
        with self.assertRaises(self.fs.FastshareError) as ctx:
            self.fs.FastshareApi("u", "spatne").login()
        self.assertIn("jméno a heslo", str(ctx.exception))
        self.assertNotIn("spatne", str(ctx.exception))
        for ref in ("fs:1:evil.com", "fs:1:data4.evil", "fs:x:data4", "https://data4.fastshare.cloud/x"):
            with self.assertRaises(self.fs.FastshareError):
                self.fs.parse_ref(ref)


class TestFastshareVJadru(unittest.TestCase):
    def test_streamy_filtr_a_prehrani(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"fs_username": "u", "fs_password": "p"}, tmp)
            self.assertTrue(engine.sources()["fastshare"])

            class Fake:
                def search(self, query, limit=25):
                    return ([{"id": "1", "server": "data4", "name": "Matrix (1999) CZ dabing.mkv", "size": 4 * 2 ** 30,
                              "size_h": "4.00 GB", "duration": 8178, "media": {"width": 1920, "height": 800}},
                             {"id": "2", "server": "data4", "name": "Matrix Reloaded (2003).mkv", "size": 1,
                              "size_h": "", "media": {}}], 2)

                def request(self, ref):
                    return "https://data4.fastshare.cloud/download.php?id=1", {"Cookie": "FASTSHARE=H"}

                def kodi_url(self, ref):
                    return "https://data4.fastshare.cloud/download.php?id=1|Cookie=FASTSHARE%3DH"

            engine._fs = Fake()
            found = engine._fastshare_streams({"name": "Matrix", "year": 1999}, None, "movie")
            self.assertEqual([s["url"] for s in found], [f"fs:1:data4:{4 * 2 ** 30}"], "Reloaded je jiný titul")
            self.assertEqual(found[0]["quality"], "Full HD")
            self.assertIn("|Cookie=", engine.resolve(found[0]["url"]))
            self.assertEqual(engine.file_request(found[0]["url"])[1], {"Cookie": "FASTSHARE=H"})

    def test_bez_uctu_neni_zdroj_a_hlavicka_se_cte(self):
        from nokturno_core import NokturnoError
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"fs_username": "u"}, tmp)
            self.assertIsNone(engine.fs)
            with self.assertRaises(NokturnoError):
                engine.resolve("fs:1:data4:1")

    def test_hlavicka_jen_s_neomezenym_stahovanim(self):
        for unlimited, cekano in ((False, []), (True, ["fs:1:data4:10"])):
            with tempfile.TemporaryDirectory() as tmp:
                engine = Engine({"audio_probe": 24}, tmp)

                class Ucet:
                    def account(self, unlimited=unlimited):
                        return {"hash": "H", "unlimited": unlimited, "credit_mb": 25000}

                engine._fs = Ucet()
                ctene = []
                engine._media_from_file = lambda url: ctene.append(url) or {}
                engine._fill_audio([{"url": "fs:1:data4:10", "label": "Film.mkv", "source": "fs"},
                                    {"url": "https://x/y.mkv", "label": "Jiny.mkv"}])
                self.assertEqual(ctene, cekano, "na kredit se hlavička FastShare nečte")


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
                self.assertEqual(len(volani), 1, "v jednom výpisu se výpadek nezkouší pětkrát (paměť v enginu)")
                engine._orig_memo.clear()   # jako po ORIG_MEMO_FAIL_S — další výpis
                engine.original_titles(meta, "movie")
                self.assertEqual(len(volani), 2, "po výpadku se má zkusit znovu, na disku se necachuje")
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


class TestSyncPodlePrijmu(unittest.TestCase):
    """Zpožděně poslaná změna musí dojít i Kodi, které se mezitím synchronizovalo."""

    def test_pozdni_push_dojde_druhemu_zarizeni(self):
        from nokturno_core.lib.store import Store
        from nokturno_core.lib.sync import apply_changes, collect_changes
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ha, office = Store(a), Store(b)
            office.add_history("any", "avatar")
            office.clear_history("any")          # ts změny je teď, push přijde až později
            zmena = collect_changes(office, 0)
            for rec in zmena["histlog"].values():
                rec["ts"] -= 600
            since_obyvak = int(time.time()) - 60  # Obývák se synchronizoval po vzniku změny
            apply_changes(ha, zmena, stamp=True)
            vrat = collect_changes(ha, since_obyvak)["histlog"]
            self.assertEqual(len(vrat), 1)
            self.assertIn("rts", next(iter(vrat.values())))

    def test_zarizeni_rts_nenosi(self):
        from nokturno_core.lib.store import Store
        from nokturno_core.lib.sync import apply_changes
        with tempfile.TemporaryDirectory() as a:
            s = Store(a)
            apply_changes(s, {"histlog": {"any\tx": {"kind": "any", "q": "x", "on": True, "ts": 5, "rts": 9}}})
            self.assertNotIn("rts", s.load("histlog", {})["any\tx"])


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

    def test_nove_pridane_serialy_s_imdb_a_nejnovejsim_dilem(self):
        from nokturno_core.lib.sosac_direct import SosacDirect, EXPORT
        d = SosacDirect()
        index = [["mesto krve", "city of blood", {"n": {"cs": "Město krve", "en": "City of Blood"}, "m": "123456",
                                                  "l": "http://x/serialy/1.json"}],
                 ["dvojnik", "a", {"n": {"cs": "Dvojník", "en": "A"}, "m": "1", "l": "http://x/serialy/2.json"}],
                 ["dvojnik", "b", {"n": {"cs": "Dvojník", "en": "B"}, "m": "2", "l": "http://x/serialy/3.json"}],
                 ["bez imdb", "", {"n": {"cs": "Bez IMDb"}, "l": "http://x/serialy/4.json"}]]
        eps = [{"t": {"cs": "Město krve", "en": "City of Blood"}, "s": "1", "e": "6"},
               {"t": {"cs": "Město krve", "en": "City of Blood"}, "s": "1", "e": "4"},
               {"t": {"cs": "Dvojník", "en": "B"}, "s": "2", "e": "1"},
               {"t": {"cs": "Bez IMDb"}, "s": "1", "e": "1"},
               {"t": {"cs": "Neznámý"}, "s": "1", "e": "1"}]
        d._series_index = lambda: index
        d._get = lambda url, ttl=None: eps if url == EXPORT + "tvshowsrecentlyadded.json" else []
        out = d.recent_series()
        self.assertEqual([(m["imdb_id"], s, e) for m, s, e in out], [("tt0123456", 1, 6), ("tt0000002", 2, 1)])

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


class TestPython38(unittest.TestCase):
    """Kodi 20 na Androidu a Windows běží s Pythonem 3.8 — co je novější, tam tiše chybí."""

    ZAKAZANE_METODY = {"removeprefix", "removesuffix", "is_relative_to", "with_stem"}

    def test_zadna_syntaxe_ani_metoda_z_3_9(self):
        chyby = []
        for path in sorted(ROOT.glob("nokturno_core/**/*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in self.ZAKAZANE_METODY:
                    chyby.append(f"{path.name}:{node.lineno} .{node.attr}()")
                if type(node).__name__ in ("Match", "MatchValue"):
                    chyby.append(f"{path.name}:{node.lineno} match")
                # `dict | dict` a `list[str]` mimo anotace jdou poznat jen za běhu — hlídá CI s 3.8
        self.assertEqual(chyby, [])


class TestOpravyZAuditu(unittest.TestCase):
    """Čtyři nálezy auditu 2026-09-14 — každý byl v ostrém provozu vidět jako pád,
    únik nebo díra, a každý má tady regresi."""

    def test_vyprsely_odkaz_streamuj_neshodi_vypis(self):
        """`SosacError` z `resolve("streamuj:…")` dřív prošla `_fill_audio` jako cizí výjimka."""
        from nokturno_core.engine import NokturnoError
        from nokturno_core.lib.sosac_direct import SosacError
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"streamuj_username": "u", "streamuj_password": "p"}, tmp)

            class VyprselyStreamuj:
                def resolve(self, url):
                    raise SosacError("streamuj: timeout")
            engine._sosac = VyprselyStreamuj()
            with self.assertRaises(NokturnoError) as ctx:
                engine.resolve("streamuj:https://www.streamuj.tv/x")
            self.assertIn("Sosáč", str(ctx.exception))
            self.assertEqual(engine._media_from_file("streamuj:https://www.streamuj.tv/x"), {})
            # ani jiná chyba při čtení hlavičky (rozbitý soubor, síť) nesmí z loaderu vylétnout
            engine.resolve = lambda url: (_ for _ in ()).throw(RuntimeError("cokoli"))
            self.assertEqual(engine._media_from_file("ws:abc"), {})
            streams = [{"url": "ws:abc", "label": "a.mkv", "detail": "1 GB", "source": "ws"}]
            self.assertEqual(engine._fill_audio(list(streams)), streams)

    def test_cteni_hlavicky_neprecte_cely_soubor(self):
        """Server, který Range ignoruje, pošle 200 a celý soubor — číst se smí jen výřez."""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from nokturno_core.lib import mediainfo

        class Zdroj(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/range":
                    self.send_response(206)
                    self.send_header("Content-Range", "bytes 0-15/4000000")
                    self.send_header("Content-Length", "16")
                    self.end_headers()
                    self.wfile.write(b"\x1a\x45\xdf\xa3" + b"\x00" * 12)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(4_000_000))
                self.end_headers()
                try:
                    for _ in range(4_000_000 // 65536):
                        self.wfile.write(b"\x00" * 65536)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Zdroj)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            self.assertEqual(mediainfo.fetch_sized(f"{base}/cely", length=16), (b"", 0))
            data, total = mediainfo.fetch_sized(f"{base}/range", length=16)
            self.assertEqual((len(data), total), (16, 4_000_000))
            info = mediainfo.probe(f"{base}/cely")
            self.assertEqual((info["audio"], info["height"], info["size"]), ([], 0, 0), "bez výřezu nemá co číst")
            # a takový „prázdný" výsledek si jádro nesmí pamatovat 30 dní (audit 1.5)
            with tempfile.TemporaryDirectory() as tmp:
                engine = Engine({}, tmp)
                engine.resolve = lambda url: f"{base}/cely"
                self.assertEqual(engine._media_from_file("ws:x")["size"], 0)
                cache = pathlib.Path(tmp) / "cache"
                self.assertEqual([p.name for p in cache.glob("*.json")] if cache.exists() else [], [])
                engine.resolve = lambda url: f"{base}/range"
                engine._media_from_file("ws:y")
                self.assertEqual(len(list(cache.glob("*.json"))), 1, "skutečně přečtená hlavička se pamatuje")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_streamuj_resolve_jen_na_streamuj(self):
        """Odkaz za `streamuj:` je z exportu Sosáče, u Stremia i z adresy od kohokoli —
        GET kamkoli do sítě (SSRF) se nesmí ani začít."""
        from nokturno_core.lib.sosac_direct import SosacDirect, SosacError, je_streamuj
        for ok in ("https://www.streamuj.tv/video/abc", "http://streamuj.tv/x", "https://cdn1.streamuj.tv/f.mp4"):
            self.assertTrue(je_streamuj(ok), ok)
        for cizi in ("http://192.168.1.10:8123/api/", "https://streamuj.tv.evil.com/x", "https://evilstreamuj.tv/",
                     "ftp://streamuj.tv/x", "file:///etc/passwd", "", "streamuj.tv/x"):
            self.assertFalse(je_streamuj(cizi), cizi)
        d = SosacDirect("u", "p")
        with self.assertRaises(SosacError) as ctx:
            d.resolve("streamuj:http://127.0.0.1:1/api")
        self.assertIn("streamuj.tv", str(ctx.exception))

    def test_token_luny_a_ucet_streamuj_nejsou_v_chybe(self):
        """Hláška jde do notifikace Kodi a do kodi.log, který lidé posílají do fór."""
        from nokturno_core.lib.luna_api import LunaApi, LunaError
        from nokturno_core.lib.sosac_direct import SosacDirect, SosacError, bez_uctu
        luna = LunaApi("http://127.0.0.1:1", "e1.tajnytoken")
        with self.assertRaises(LunaError) as ctx:
            luna.meta("movie", "tt1")
        self.assertNotIn("tajnytoken", str(ctx.exception))
        self.assertIn("127.0.0.1:1", str(ctx.exception), "adresa serveru v hlášce zůstává, pomáhá ladit")
        self.assertEqual(bez_uctu("https://www.streamuj.tv/json_api_player.php?action=x&login=ja&password=abc&location=1"),
                         "https://www.streamuj.tv/json_api_player.php?action=x&login=%E2%80%A6&password=%E2%80%A6&location=1")
        with self.assertRaises(SosacError) as ctx:
            SosacDirect("ja", "tajne")._get("http://127.0.0.1:1/x?login=ja&password=abcdef")
        self.assertNotIn("abcdef", str(ctx.exception))
        self.assertIn("127.0.0.1:1/x", str(ctx.exception))


from nokturno_core.engine import NokturnoError as NokturnoError_  # noqa: E402


class TestVykonZdroju(unittest.TestCase):
    """Audit 2026-09-14, výkon: čtyři zdroje streamů souběžně, originální názvy jednou
    za výpis, WebShare po selhání loginu zkusí znovu, re-login jen na odmítnutí serverem."""

    def _engine(self, tmp, zpozdeni=0.3):
        engine = Engine({}, tmp)
        engine.meta = lambda ctype, item_id, series_id=None: ({"id": item_id, "name": "Film", "year": 2020}, None)
        engine.api_for = lambda item_id: (_ for _ in ()).throw(NokturnoError_("není nastaven zdroj"))
        engine._webshare_subtitles = lambda *a, **k: []
        engine._fill_audio = lambda streams, *a, **k: streams
        engine._storage_streams = lambda *a, **k: []
        poradi = []

        def zdroj(jmeno, url):
            def fetch(*a, **k):
                time.sleep(zpozdeni)
                poradi.append(jmeno)
                return [{"url": url, "label": f"{jmeno}.mkv", "detail": "1 GB", "source": jmeno, "_direct": True}]
            return fetch
        engine._cross_streams = zdroj("main", "streamuj:https://www.streamuj.tv/1")
        engine._webshare_streams = zdroj("ws", "ws:1")
        engine._hellspy_streams = zdroj("hs", "hs:1:a")
        engine._sledujteto_streams = zdroj("st", "st:1")
        engine._merge_direct = lambda found: found
        return engine, poradi

    def test_zdroje_bezi_soubezne_a_drzi_poradi(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, _poradi = self._engine(tmp, zpozdeni=0.3)
            start = time.time()
            out = engine.streams("movie", "tt1")
            self.assertLess(time.time() - start, 0.9, "čtyři zdroje po 0,3 s musí doběhnout dřív než za 1,2 s")
            self.assertEqual([s["source"] for s in out], ["Luna", "WebShare", "HellSpy", "Sledujteto"], "pořadí zdrojů drží")

    def test_padly_zdroj_nezastavi_ostatni_a_hlasi_se(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, _ = self._engine(tmp, zpozdeni=0)

            def spadne(*a, **k):
                raise RuntimeError("HellSpy mimo provoz")
            engine._hellspy_streams = spadne
            failures = []
            out = engine.streams("movie", "tt2", failures=failures)
            self.assertEqual([s["source"] for s in out], ["Luna", "WebShare", "Sledujteto"])
            self.assertEqual([f[0] for f in failures], ["HellSpy"])

    def test_hellspy_429_ukonci_hledani_po_prvnim_dotazu(self):
        """429 = omezená IP: další dotazy stejného hledání se neposílají, chyba se hlásí jednou."""
        from nokturno_core.lib.hellspy_api import HellspyRateLimited
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"hs_enabled": True}, tmp)
            dotazy = []

            class Hs:
                def search(self, query, limit=40, offset=0):
                    dotazy.append(query)
                    raise HellspyRateLimited("HTTP 429")
            engine._hs = Hs()
            engine._title_queries = lambda *a, **k: (["a", "b", "c"], lambda name: True)
            failures = []
            out = engine._hellspy_streams({"name": "Film", "year": 2020}, failures=failures)
            self.assertEqual(out, [])
            self.assertEqual(dotazy, ["a"])
            self.assertEqual([f[0] for f in failures], ["HellSpy"])

    def test_hellspy_po_429_se_dalsi_dotazy_neposilaji_ani_pro_jiny_titul(self):
        """Stavba katalogu volá HellSpy pro desítky titulů — po první 429 pauza bez sítě."""
        import urllib.error
        from unittest import mock
        from nokturno_core.lib import hellspy_api
        hellspy_api._blocked_until = 0.0
        try:
            api = hellspy_api.HellspyApi()
            volani = []

            def urlopen(req, timeout=0):
                volani.append(req.full_url)
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)
            with mock.patch.object(hellspy_api.urllib.request, "urlopen", urlopen):
                with self.assertRaises(hellspy_api.HellspyRateLimited) as prvni:
                    api.search("Film 1")
                self.assertFalse(prvni.exception.paused)
                for i in range(2, 6):
                    with self.assertRaises(hellspy_api.HellspyRateLimited) as dalsi:
                        api.search(f"Film {i}")
                    self.assertTrue(dalsi.exception.paused)
            self.assertEqual(len(volani), 1)
            self.assertGreater(hellspy_api.blocked_for(), 0)
            hellspy_api._blocked_until = 0.0        # pauza vyprší → zase se volá
            with mock.patch.object(hellspy_api.urllib.request, "urlopen", urlopen):
                with self.assertRaises(hellspy_api.HellspyRateLimited):
                    api.search("Film 7")
            self.assertEqual(len(volani), 2)
        finally:
            hellspy_api._blocked_until = 0.0

    def test_original_titles_jednou_za_vypis(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"streamuj_username": "u", "streamuj_password": "p"}, tmp)
            volani = []

            class Sosac:
                def meta(self, ctype, alt):
                    volani.append(alt)
                    return {"_orig": "The Matrix", "_title": "Matrix"}
            engine._sosac = Sosac()
            meta = {"id": "sosacd_1", "_title": "Matrix", "year": 1999}
            for _ in range(5):
                self.assertEqual(engine.original_titles(meta, "movie", "sosacd_1"), ["The Matrix"])
            self.assertEqual(volani, ["sosacd_1"], "pět zdrojů = jeden dotaz, ne pět")
            self.assertEqual(engine.original_titles({"id": "sosacd_2", "_title": "Jiný"}, "movie", "sosacd_2"),
                             ["The Matrix", "Matrix"])
            self.assertEqual(volani, ["sosacd_1", "sosacd_2"], "jiný titul se počítá znovu")

    def test_webshare_po_selhani_loginu_zkusi_znovu(self):
        from unittest import mock
        from nokturno_core import engine as modul
        from nokturno_core.lib.webshare_api import WebshareError
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"ws_username": "u", "ws_password": "p"}, tmp)
            with mock.patch.object(modul.WebshareApi, "login", side_effect=[WebshareError("timeout"), "tok"]) as login:
                self.assertIsNone(engine.ws, "první login selhal")
                self.assertIsNone(engine.ws, "do WS_RETRY_S se nezkouší")
                self.assertEqual(login.call_count, 1)
                engine._ws_retry_after = 0
                self.assertIsNotNone(engine.ws, "po odstupu se zkusí znovu a uspěje")
                self.assertIsNotNone(engine.ws)
                self.assertEqual(login.call_count, 2, "úspěch se pamatuje")

    def test_relogin_jen_na_odmitnuti_serverem(self):
        from nokturno_core.lib.webshare_api import WebshareApi, WebshareApiError, WebshareError
        api = WebshareApi("u", "p", token="t")
        logins = []

        def login():
            logins.append(1)
            api.token = "t2"
            return "t2"
        api.login = login

        def sit(endpoint, **data):
            raise WebshareError("search: <urlopen error timed out>")
        api._call = sit
        with self.assertRaises(WebshareError):
            api._with_token("search", what="x")
        self.assertEqual(logins, [], "síťová chyba = žádný re-login (dřív salt+login+opakování)")

        pokusy = []

        def odmitnuto(endpoint, **data):
            pokusy.append(data.get("wst"))
            if len(pokusy) == 1:
                raise WebshareApiError("search: Invalid token")
            return "ok"
        api._call = odmitnuto
        self.assertEqual(api._with_token("search", what="x"), "ok")
        self.assertEqual(logins, [1])
        self.assertEqual(pokusy, ["t", "t2"])


class TestFreshCache(unittest.TestCase):
    """`fresh=True`: API cache jen zapisuje, nečte — zahřívání na pozadí v Kodi tak obnoví
    i to, čemu TTL ještě neprošlo (dřív s TTL rovným intervalu warm-up nic neobnovil)."""

    def test_luna_a_sosac_s_fresh_ctou_s_ttl_nula(self):
        from nokturno_core.lib.luna_api import LunaApi
        from nokturno_core.lib.sosac_direct import SosacDirect
        videno = []

        class Cache:
            def cached(self, key, ttl, loader):
                videno.append(ttl)
                return {"x": 1}
        LunaApi("http://luna", "t", cache=Cache(), cache_ttl=600)._get_cached("http://luna/a")
        LunaApi("http://luna", "t", cache=Cache(), cache_ttl=600, fresh=True)._get_cached("http://luna/a")
        SosacDirect(cache=Cache(), cache_ttl=600)._get("http://s/a", ttl=3600)
        SosacDirect(cache=Cache(), cache_ttl=600, fresh=True)._get("http://s/a", ttl=3600)
        self.assertEqual(videno, [600, 0, 3600, 0])


class TestDavkaPredVydanim(unittest.TestCase):
    """Audit 2026-09-14, drobné, ale skutečné chyby před vydáním 3.1.12."""

    def test_torrenty_konkretni_dil_pred_balikem_sezony(self):
        from nokturno_core.lib.prowlarr import ProwlarrApi
        rows = [{"title": "Serial.S02.Complete.1080p", "seeders": 50, "size_gb": 40},
                {"title": "Serial.S02E01.1080p", "seeders": 5, "size_gb": 2},
                {"title": "Serial.2x01.720p", "seeders": 8, "size_gb": 1}]
        self.assertEqual([r["seeders"] for r in ProwlarrApi._order(rows, episode=1)], [8, 5, 50],
                         "díl napřed (podle seedů), balík až za ním")
        self.assertEqual([r["seeders"] for r in ProwlarrApi._order(rows)], [50, 8, 5], "u filmu jen seedy")

    def test_stats_snese_soubeh(self):
        import threading
        from nokturno_core.lib.stats import Stats
        stats = Stats(tempfile.mkdtemp())
        chyby = []

        def pis(n):
            try:
                for i in range(150):
                    stats.note_play(f"tt{n}{i}", "Film", 2020, "movie")
                    stats.note_use()
                    stats.payload(version="1")
            except Exception as err:  # noqa: BLE001
                chyby.append(repr(err))
        vlakna = [threading.Thread(target=pis, args=(n,)) for n in range(6)]
        for v in vlakna:
            v.start()
        for v in vlakna:
            v.join(20)
        self.assertEqual(chyby, [])
        self.assertLessEqual(len(stats.payload()["plays"]), 300)

    def test_rozbity_export_sosace_je_sosac_error(self):
        from nokturno_core.lib.sosac_direct import SosacDirect, SosacError

        class Cache:
            def __init__(self, data):
                self.data = data

            def cached(self, key, ttl, loader):
                return self.data
        with self.assertRaises(SosacError):
            SosacDirect(cache=Cache({"error": "maintenance"})).catalog("movie", "moviesmostpopular")
        with self.assertRaises(SosacError):
            SosacDirect(cache=Cache({"q": []}))._search("movie", "matrix")
        with self.assertRaises(SosacError):
            SosacDirect(cache=Cache("html")).episodes("42")
        # neciferný klíč sezóny („speciály“) se přeskočí, nesmí shodit seznam dílů
        d = SosacDirect(cache=Cache([{"1": {"1": {"n": "Pilot", "l": "x"}}, "special": {"a": {}}}]))
        self.assertEqual([(v["season"], v["episode"], v["title"]) for v in d.episodes("42")], [(1, 1, "Pilot")])

    def test_hledani_s_vypadkem_zdroje_se_necachuje(self):
        from nokturno_core import engine as modul
        from nokturno_core.lib.sosac_direct import SosacError
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({}, tmp)
            volani = []

            class SpadlySosac:
                def catalog(self, *a, **k):
                    raise SosacError("export 503")

            class Cinemeta:
                def catalog(self, ctype, cid, search=""):
                    volani.append(search)
                    return [{"id": "tt1", "name": "Matrix", "year": 1999, "type": "movie"}]
            engine._sosac_db, engine._cinemeta = SpadlySosac(), Cinemeta()
            puvodni = modul.enrich
            modul.enrich = lambda *a, **k: None
            try:
                self.assertEqual(len(engine.search("movie", "matrix")), 1, "degradovaný výsledek se ukáže")
                engine.search("movie", "matrix")
                self.assertEqual(len(volani), 2, "ale nepamatuje se — druhé hledání jde znovu na zdroje")
            finally:
                modul.enrich = puvodni


class TestVykonJadra2(unittest.TestCase):
    """Druhá dávka výkonu: TMDB bez N+1, sezóny souběžně, index seriálů Sosáče, jeden executor
    v enrich, značka úložiště jednou za minutu, jedna cache hledání Luny."""

    def test_tmdb_katalog_jeden_dotaz_na_polozku(self):
        from nokturno_core.lib.tmdb_api import TmdbApi
        api = TmdbApi("k")
        cesty = []

        def get(path, **params):
            cesty.append(path)
            if path.startswith("/genre/"):
                return {"genres": [{"id": 1, "name": "Akční"}]}
            if path.startswith("/discover/"):
                return {"results": [{"id": 10, "title": "A", "genre_ids": [1]}, {"id": 11, "title": "B"}]}
            if path in ("/movie/10", "/movie/11"):
                self.assertEqual(params.get("append_to_response"), "external_ids,images")
                return {"external_ids": {"imdb_id": f"tt{path[-2:]}"}, "images": {"backdrops": [], "logos": []}}
            raise AssertionError(path)
        api._get = get
        items = api.catalog("movie", "popular")
        self.assertEqual([i["id"] for i in items], ["tt10", "tt11"])
        self.assertEqual(len([c for c in cesty if c.startswith("/movie/")]), 2, "1 dotaz na položku, ne 2")
        self.assertEqual(items[0]["genres"], ["Akční"])

    def test_tmdb_imdb_id_preklada_a_cachuje(self):
        """`imdb_id()` pro klienty Stremia, kteří posílají `tmdb:<id>` místo `tt…`."""
        import tempfile
        from nokturno_core.lib.store import Store
        from nokturno_core.lib.tmdb_api import TmdbApi
        api = TmdbApi("k", cache=Store(tempfile.mkdtemp()))
        cesty = []

        def get(path, **params):
            cesty.append(path)
            if path == "/tv/37738":
                return {"external_ids": {"imdb_id": "tt1592598"}}
            if path == "/movie/999":
                return {"external_ids": {}}          # TMDB titul zná, IMDb id nemá
            return None                               # TMDB titul nezná vůbec
        api._get = get
        self.assertEqual(api.imdb_id("series", 37738), "tt1592598")
        self.assertEqual(api.imdb_id("series", 37738), "tt1592598")
        self.assertEqual(cesty.count("/tv/37738"), 1, "detail je cachovaný, ne dotaz na požadavek")
        self.assertEqual(api.imdb_id("movie", 999), "")
        self.assertEqual(api.imdb_id("movie", 12345), "")

    def test_tmdb_detail_obsazeni_hlasy_trailer_rating(self):
        """Obsazení s fotkou a rolí, počet hlasů, věkový rating (přednost CZ před US)
        a YouTube id traileru (přednost oficiálnímu traileru před jiným videem)."""
        from nokturno_core.lib.tmdb_api import TmdbApi
        api = TmdbApi("k")

        def get(path, **params):
            if path.startswith("/find/"):
                return {"movie_results": [{"id": 7}]}
            if path == "/movie/7":
                self.assertEqual(params.get("append_to_response"), "credits,images,videos,release_dates")
                return {
                    "title": "Film", "vote_average": 8.1, "vote_count": 12345, "images": {},
                    "credits": {"cast": [{"name": "Herec", "character": "Role", "profile_path": "/h.jpg"},
                                        {"name": "Bez fotky", "character": "X"}]},
                    "release_dates": {"results": [
                        {"iso_3166_1": "US", "release_dates": [{"certification": "R"}]},
                        {"iso_3166_1": "CZ", "release_dates": [{"certification": "15"}]},
                    ]},
                    "videos": {"results": [{"site": "YouTube", "type": "Teaser", "key": "teaser1"},
                                           {"site": "YouTube", "type": "Trailer", "key": "trailer1"}]},
                }
            raise AssertionError(path)
        api._get = get
        meta = api.meta("movie", "tt7")
        self.assertEqual(meta["cast"], [{"name": "Herec", "character": "Role",
                                         "photo": "https://image.tmdb.org/t/p/w500/h.jpg"},
                                        {"name": "Bez fotky", "character": "X", "photo": ""}])
        self.assertEqual(meta["voteCount"], 12345)
        self.assertEqual(meta["mpaa"], "15", "český rating má přednost před americkým")
        self.assertEqual(meta["trailerYoutubeId"], "trailer1", "skutečný trailer má přednost před teaserem")

    def test_tmdb_serial_rating_bez_ceskeho_spadne_na_americky(self):
        from nokturno_core.lib.tmdb_api import TmdbApi
        api = TmdbApi("k")

        def get(path, **params):
            if path.startswith("/find/"):
                return {"tv_results": [{"id": 8}]}
            if path == "/tv/8":
                self.assertEqual(params.get("append_to_response"), "credits,images,videos,content_ratings")
                return {"name": "Serial", "images": {},
                       "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-14"}]}}
            raise AssertionError(path)
        api._get = get
        meta = api.meta("series", "tt8")
        self.assertEqual(meta["mpaa"], "TV-14")
        self.assertEqual(meta["voteCount"], 0)
        self.assertEqual(meta["trailerYoutubeId"], "")

    def test_tmdb_sezony_soubezne(self):
        from nokturno_core.lib.tmdb_api import TmdbApi
        api = TmdbApi("k")

        def get(path, **params):
            if path.startswith("/find/"):
                return {"tv_results": [{"id": 5}]}
            if path == "/tv/5":
                return {"name": "Serial", "seasons": [{"season_number": n} for n in range(1, 7)], "images": {}}
            if path.startswith("/tv/5/season/"):
                time.sleep(0.2)
                n = int(path.rsplit("/", 1)[1])
                return {"episodes": [{"episode_number": 1, "name": f"S{n}E1"}]}
            raise AssertionError(path)
        api._get = get
        start = time.time()
        meta = api.meta("series", "tt5")
        self.assertLess(time.time() - start, 0.8, "6 sezón po 0,2 s souběžně, ne 1,2 s za sebou")
        self.assertEqual(len(meta["videos"]), 6)
        self.assertEqual(meta["videos"][0]["id"], "tt5:1:1")

    def test_sosac_index_serialu_se_stavi_jednou(self):
        from nokturno_core.lib.sosac_direct import SosacDirect
        stazeno = []

        class Cache:
            def __init__(self):
                self.data = {}

            def cached(self, key, ttl, loader):
                if key not in self.data:
                    self.data[key] = loader()
                return self.data[key]
        d = SosacDirect(cache=Cache())
        d._get = lambda url, ttl=None: (stazeno.append(url) or
                                        ([{"n": {"cs": "Matrix seriál", "en": "Matrix"}, "l": "x", "i": ""}] if url.endswith("/m.json") else []))
        self.assertEqual([m["_title"] for m in d._search("series", "matrix")], ["Matrix seriál"])
        d._search("series", "matrix")
        d._search("series", "jiny")
        self.assertEqual(len(stazeno), 27, "27 písmen jednou, ne při každém dotazu")
        self.assertIn("sosac:tvindex", d.cache.data)

    def test_enrich_sdili_executor_a_nedotahuje_titul_dvakrat(self):
        import threading
        from nokturno_core.lib import enrich as modul
        self.assertNotIn("ThreadPoolExecutor(max_workers=WORKERS)\n    futures", pathlib.Path(modul.__file__).read_text())
        volani = []
        brzda = threading.Event()
        puvodni = modul._lookup

        def pomale(luna, store, ctype, meta):
            volani.append(meta["imdb_id"])
            brzda.wait(1)
            return {"description": "popis"}
        modul._lookup = pomale
        try:
            a = [{"imdb_id": "tt1", "name": "A"}, {"imdb_id": "tt1", "name": "A dup"}, {"imdb_id": "tt2", "name": "B"}]
            t = threading.Thread(target=lambda: modul.enrich(a, deadline=3))
            t.start()
            time.sleep(0.2)
            b = [{"imdb_id": "tt1", "name": "A znovu"}]
            modul.enrich(b, deadline=3)   # tt1 už běží — druhé hledání se na něj jen napojí
            brzda.set()
            t.join(5)
            self.assertEqual(sorted(volani), ["tt1", "tt2"], "tentýž titul jednou, i ze dvou hledání naráz")
            self.assertEqual([m.get("description") for m in a + b], ["popis"] * 4)
        finally:
            modul._lookup = puvodni

    def test_enrich_okamzity_vysledek_nezpusobi_deadlock(self):
        """`_submit()` registruje `add_done_callback` až PO zápisu do `_INFLIGHT` — když
        `_lookup` doběhne dřív, než se `add_done_callback` stihne zavolat (bez Luny/sítě
        vrací `_fetch_title` prázdno okamžitě), spustí se callback rovnou v témž vlákně,
        ještě uvnitř `with _INFLIGHT_LOCK:`. S prostým `Lock` (ne `RLock`) to byl jistý
        deadlock — 2026-09-15, odhaleno při stavbě klasifikace CZ dabing/titulky pro Kodi."""
        import threading
        from nokturno_core.lib import enrich as modul
        metas = [{"name": f"Film {i}"} for i in range(5)]   # bez luna/imdb_id → _fetch_title vrátí {} hned
        hotovo = threading.Event()
        t = threading.Thread(target=lambda: (modul.enrich(metas, luna=None, deadline=3), hotovo.set()))
        t.start()
        self.assertTrue(hotovo.wait(4), "enrich() se zaseknul (deadlock v _INFLIGHT_LOCK)")
        t.join(1)

    def test_enrich_doplni_hodnoceni_i_kdyz_uz_ma_popis(self):
        """Sosáčův export nosí krátký popis skoro vždy, ale hodnocení jen občas —
        `_needs()` dřív titul s popisem, ale bez hodnocení, považoval za hotový a
        `imdbRating` mu už nikdy nedotáhl (2026-09-15, nahlásil uživatel: „některé
        filmy nemají hodnocení“)."""
        from nokturno_core.lib import enrich as modul
        meta = {"imdb_id": "tt1", "name": "Film", "description": "krátký popis ze Sosáče"}
        puvodni = modul._lookup
        modul._lookup = lambda luna, store, ctype, m: {"imdbRating": 7.5}
        try:
            self.assertEqual(modul.enrich([meta], deadline=3), 1)
            self.assertEqual(meta["imdbRating"], 7.5)
        finally:
            modul._lookup = puvodni

    def test_fetch_doplni_hodnoceni_z_cinemety_i_kdyz_luna_ma_popis(self):
        """`_fetch()` dřív sáhl na Cinemetu, jen když Luna nedala vůbec popis —
        Luna ale umí vrátit popis BEZ hodnocení (2026-09-15, ověřeno u titulů ze
        Sosáčova „nově přidané“: Cinemeta hodnocení měla, Luna popis bez něj), takže
        se hodnocení nikdy nedotáhlo. Český popis z Luny se přitom nesmí ztratit."""
        from nokturno_core.lib import enrich as modul

        class FakeLuna:
            def meta(self, ctype, imdb):
                return {"description": "český popis z Luny"}   # bez imdbRating
        puvodni = modul._cinemeta
        modul._cinemeta = lambda ctype, imdb: {"description": "english plot", "imdbRating": 7.5}
        try:
            data = modul._fetch(FakeLuna(), None, "movie", "tt1474311")
            self.assertEqual(data["description"], "český popis z Luny")
            self.assertEqual(data["imdbRating"], 7.5)
        finally:
            modul._cinemeta = puvodni

    def test_znacka_uloziste_jednou_za_minutu(self):
        from nokturno_core.lib.storage_api import StorageApi
        api = StorageApi("http://nas/dav/")
        cteni = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self, n):
                return b"123.4"
        api._open = lambda url, **k: cteni.append(url) or Resp()
        self.assertEqual(api.revision(), "123.4")
        self.assertEqual(api.revision(), "123.4")
        self.assertEqual(len(cteni), 1)
        api._rev = (0.0, "")
        api.revision()
        self.assertEqual(len(cteni), 2)

    def test_hledani_luny_ma_jednu_cache(self):
        src = (ROOT / "nokturno_core" / "engine.py").read_text(encoding="utf-8")
        self.assertNotIn("luna:search:", src)


class TestUdrzbaJadra(unittest.TestCase):
    def test_jedna_human_size_a_fold(self):
        from nokturno_core.lib import streams, webshare_api, hellspy_api, sledujteto_api, storage_api
        for m in (webshare_api, hellspy_api, sledujteto_api, storage_api):
            self.assertIs(m.human_size, streams.human_size, m.__name__)
        self.assertEqual(streams.human_size(4.5 * 2 ** 30), "4.5 GB")
        self.assertEqual(streams.human_size(512 * 2 ** 20), "512 MB")
        self.assertEqual(streams.human_size("x"), "")
        self.assertEqual(streams.human_size(0), "")
        self.assertEqual(streams.parse_size_gb(streams.human_size(2 * 2 ** 30)), 2.0, "co napíšeme, i přečteme")
        from nokturno_core import engine
        self.assertIs(engine._fold, streams.fold)
        self.assertIs(storage_api._fold, streams.fold)
        self.assertEqual(streams.fold("Pět švestek"), "pet svestek")

    def test_user_agent_nelze_o_hostiteli(self):
        for path in sorted(ROOT.glob("nokturno_core/**/*.py")):
            if path.name == "stats.py":
                continue   # `agent` je parametr — hostitel ho posílá sám, výchozí hodnota patří Kodi
            src = path.read_text(encoding="utf-8")
            for lez in ("Kodi plugin.video.nokturno", "Home Assistant Nokturno"):
                self.assertNotIn(lez, src, f"{path.name}: UA tvrdí, že je {lez}")

    def test_bez_diagnostickeho_leseni_sledujteto(self):
        from nokturno_core.lib.sledujteto_api import SledujtetoApi
        api = SledujtetoApi("a@b.cz", "x")
        for attr in ("last_keys", "last_me_keys", "last_sample"):
            self.assertFalse(hasattr(api, attr), attr)

    def test_search_catalog_pres_cinemeta_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({}, tmp)

            class Cinemeta:
                def catalog(self, ctype, cid, genre=None, search=None, skip=0):
                    return [{"id": "tt1", "name": "Matrix", "releaseInfo": "1999", "poster": "p", "description": "d"},
                            {"id": "tt2", "name": "Matrix 4", "releaseInfo": "2021"}]
            engine._cinemeta = Cinemeta()
            out = engine.search_catalog("movie", "matrix 1999")
            self.assertEqual([(o["id"], o["year"], o["source"]) for o in out], [("tt1", 1999, "katalog")])
            src = (ROOT / "nokturno_core" / "engine.py").read_text(encoding="utf-8")
            self.assertNotIn("v3-cinemeta.strem.io", src, "Cinemeta jen přes CinemetaApi")


class TestProrezavaniCache(unittest.TestCase):
    def test_prune_cache_smaze_jen_stare(self):
        import os
        from nokturno_core.lib.store import Store
        tmp = tempfile.mkdtemp()
        store = Store(tmp)
        store.cached("a", 10, lambda: {"x": 1})
        store.cached("b", 10, lambda: {"x": 2})
        cache = pathlib.Path(tmp) / "cache"
        stary = sorted(cache.glob("*.json"))[0]
        os.utime(stary, (time.time() - 5 * 86400,) * 2)
        self.assertEqual(store.prune_cache(72 * 3600), 1)
        self.assertEqual(len(list(cache.glob("*.json"))), 1)
        self.assertEqual(Store(tempfile.mkdtemp()).prune_cache(), 0, "bez složky cache nic")


class TestWebshareTitulky(unittest.TestCase):
    """`_webshare_subtitles()` — fulltext WebShare je volný, filtr musí být přísný.

    Ostrý případ z 2026-09-18: „Outlander: Blood of My Blood" S02E01 dostal titulky
    k S01E04 a S01E05, protože se hlídala jen slova názvu.
    """

    SOUBORY = [
        ("Outlander.Blood.of.my.Blood.S01E04.720p.WEB-DL-Mafi10.srt", "srt", "a"),
        ("outlander.blood.of.my.blood.s01e05.1080p.web.h264-Mafi10.srt", "srt", "b"),
        ("Outlander.Blood.of.my.Blood.S02E01.1080p.WEB.h264 CZ.srt", "srt", "c"),
        ("Outlander.Blood.of.my.Blood.S02E01.720p.WEB-DL SK.srt", "srt", "d"),
        ("Outlander.Blood.of.my.Blood.S02E01.1080p.WEB.h264.srt", "srt", "e"),
        ("Outlander.Blood.of.my.Blood.S02E01.1080p.WEB.h264.mkv", "video", "f"),
        ("Agents of SHIELD S02E01 - The Frenemy of My Enemy.srt", "srt", "g"),
    ]

    def _engine(self, tmp, pref="CZ", soubory=None):
        import xml.etree.ElementTree as ET
        engine = Engine({"pref_lang": pref}, tmp)

        class Ws:
            def _with_token(self, endpoint, **data):
                root = ET.Element("response")
                for name, kind, ident in (soubory if soubory is not None else TestWebshareTitulky.SOUBORY):
                    f = ET.SubElement(root, "file")
                    ET.SubElement(f, "name").text = name
                    ET.SubElement(f, "type").text = kind
                    ET.SubElement(f, "ident").text = ident
                return root
        engine._ws, engine._ws_ready = Ws(), True
        engine.original_titles = lambda *a, **k: ["Cizinka: Krev mé krve"]
        return engine

    META = {"id": "tt18332852", "name": "Outlander: Blood of My Blood",
            "_title": "Outlander: Blood of My Blood"}

    def test_jen_pozadovany_dil(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            refs = engine._webshare_subtitles(self.META, {"season": 2, "episode": 1}, "series")
            self.assertNotIn("ws:a", refs, "titulky k S01E04 nepatří k S02E01")
            self.assertNotIn("ws:b", refs)
            self.assertNotIn("ws:f", refs, "video není titulek")
            self.assertNotIn("ws:g", refs, "jiný seriál")
            self.assertEqual(set(refs), {"ws:c", "ws:d", "ws:e"})

    def test_preferovany_jazyk_prvni(self):
        with tempfile.TemporaryDirectory() as tmp:
            refs = self._engine(tmp, "CZ")._webshare_subtitles(
                self.META, {"season": 2, "episode": 1}, "series")
            self.assertEqual(refs, ["ws:c", "ws:d", "ws:e"], "CZ, pak SK, pak neoznačené")
            refs = self._engine(tmp, "SK")._webshare_subtitles(
                self.META, {"season": 2, "episode": 1}, "series")
            self.assertEqual(refs, ["ws:d", "ws:c", "ws:e"], "s předvolbou SK naopak")

    def test_bez_dilu_v_nazvu_nic(self):
        soubory = [("Outlander.Blood.of.my.Blood.S01E04.720p CZ.srt", "srt", "a")]
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp, "CZ", soubory)
            self.assertEqual(engine._webshare_subtitles(self.META, {"season": 2, "episode": 1}, "series"), [],
                             "radši žádné titulky než titulky k jinému dílu")

    def test_film_se_dil_nekontroluje(self):
        soubory = [("Matrix.1999.1080p CZ.srt", "srt", "a")]
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp, "CZ", soubory)
            engine.original_titles = lambda *a, **k: []
            meta = {"id": "tt0133093", "name": "Matrix", "_title": "Matrix", "year": 1999}
            self.assertEqual(engine._webshare_subtitles(meta, None, "movie"), ["ws:a"])

    def test_rank_jazyka(self):
        from nokturno_core.engine import _subtitle_rank
        poradi = ("CZ", "SK")
        self.assertEqual(_subtitle_rank("film.cz.srt", poradi), 0)
        self.assertEqual(_subtitle_rank("film.sk.srt", poradi), 1)
        self.assertEqual(_subtitle_rank("film.web-dl.srt", poradi), 2, "bez značky za preferované")
        self.assertEqual(_subtitle_rank("film.eng.srt", poradi), 3, "cizí jazyk nakonec")
        self.assertEqual(_subtitle_rank("film.cz.srt", ()), 0, "bez předvolby se pořadí z WebShare nemění")

    def test_rank_madarstiny(self):
        from nokturno_core.engine import _subtitle_rank
        poradi = ("HU",)
        self.assertEqual(_subtitle_rank("film.hu.srt", poradi), 0)
        self.assertEqual(_subtitle_rank("film.magyar.srt", poradi), 0)
        self.assertEqual(_subtitle_rank("film.cz.srt", poradi), 2, "cizí jazyk nakonec")


class TestOpenSubtitlesVJadru(unittest.TestCase):
    """Zapojení OpenSubtitles do `raw_streams()` — úloha vedle titulků z WebShare.

    Hlídá tři věci, na kterých to stojí: že se výpadek titulků nepočítá mezi výpadky
    zdrojů (jinak by se seznam streamů přestal cachovat), že se odkaz na titulky
    přilepí jen tam, kde opravdu žádné nejsou (každé stažení jde z denní kvóty
    uživatele), a že `resolve()` umí `os:` rozklíčovat.
    """

    META = {"id": "tt0133093", "imdb_id": "tt0133093", "name": "Matrix", "_title": "Matrix"}

    def _engine(self, tmp, nalez=("os:11",)):
        engine = Engine({"pref_lang": "CZ", "os_key": "k" * 32}, tmp)
        engine._opensubtitles_subtitles = lambda *a, **k: list(nalez)
        engine._webshare_subtitles = lambda *a, **k: []
        engine.meta = lambda *a, **k: (self.META, None)
        return engine

    def test_titulky_nejsou_zdroj(self):
        """Obě titulkové úlohy musí být vyjmuté ze stejných míst jako dřív jen WebShare."""
        from nokturno_core.engine import OSUB_TASK, SUBS_TASK, SUBS_TASKS
        self.assertEqual(set(SUBS_TASKS), {SUBS_TASK, OSUB_TASK})
        zdroj = pathlib.Path("nokturno_core/engine.py").read_text(encoding="utf-8")
        self.assertNotIn("label != SUBS_TASK", zdroj,
                         "porovnání s jedinou úlohou by OpenSubtitles počítalo mezi výpadky zdrojů")

    def test_prilepi_se_jen_bez_jinych_titulku(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)
            streamy = [
                {"url": "ws:1", "label": "Matrix.1999.1080p.mkv", "_direct": True, "subtitles": []},
                # české titulky přímo v souboru (pozná je `parse_stream` z názvu)
                {"url": "ws:2", "label": "Matrix.1999.1080p.CZtit.mkv", "_direct": True, "subtitles": []},
                {"url": "ws:3", "label": "Matrix.1999.720p.mkv", "_direct": True, "subtitles": ["ws:x"]},
            ]
            engine._webshare_streams = lambda *a, **k: streamy
            for jmeno in ("_hellspy_streams", "_sledujteto_streams", "_fastshare_streams",
                          "_cztor_streams", "_storage_streams", "_cross_streams"):
                setattr(engine, jmeno, lambda *a, **k: [])
            engine.api_for = lambda *a, **k: None
            vysledek = engine.raw_streams("movie", "tt0133093", probe_audio=True)
            podle_url = {s["url"]: s.get("subtitles") for s in vysledek}
            self.assertEqual(podle_url["ws:1"], ["os:11"], "bez titulků → OpenSubtitles")
            self.assertEqual(podle_url["ws:2"], [], "vlastní české titulky v souboru stačí")
            self.assertEqual(podle_url["ws:3"], ["ws:x"], "titulky z WebShare mají přednost")

    def test_resolve_os_odkazu(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = self._engine(tmp)

            class Api:
                def odkaz(self, file_id):
                    return "https://opensubtitles.example/soubor/%d" % file_id

            engine._osub = Api()
            self.assertEqual(engine.resolve("os:42"), "https://opensubtitles.example/soubor/42")
            with self.assertRaises(NokturnoError):
                engine.resolve("os:../../etc/passwd")

    def test_seriál_bez_cisla_dilu_se_neptá(self):
        """Dotaz bez čísla dílu by vrátil titulky k celé sérii — to je ta chyba z 5.2.34."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"pref_lang": "CZ", "os_key": "k" * 32}, tmp)
            volano = []
            engine._osub = type("A", (), {
                "hledej_cachovane": lambda self, *a, **k: volano.append(a) or []})()
            self.assertEqual(engine._opensubtitles_subtitles(self.META, {"season": 1}, "series"), [])
            self.assertEqual(volano, [])

    def test_bez_imdb_id_se_neptá(self):
        """Podle názvu se tu nehledá schválně — jen tak může přijít cizí titul."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine({"pref_lang": "CZ", "os_key": "k" * 32}, tmp)
            volano = []
            engine._osub = type("A", (), {
                "hledej_cachovane": lambda self, *a, **k: volano.append(a) or []})()
            self.assertEqual(engine._opensubtitles_subtitles({"id": "sosac:123", "name": "X"}), [])
            self.assertEqual(volano, [])

    def test_bez_klice_je_zdroj_vypnuty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(Engine({"pref_lang": "CZ"}, tmp).osub)
            self.assertIsNone(Engine({"os_key": "k" * 32, "os_enabled": False}, tmp).osub)


class TestSpolecneUloziste(unittest.TestCase):
    """`Engine(shared_store=)`: co na účtu nezávisí, jde do společného úložiště
    (Stremio — jeden proces, stovky nastavení); tokeny a streamy zůstávají v tom
    vlastním. Bez `shared_store` je obojí totéž (Kodi, HA)."""

    def setUp(self):
        from nokturno_core.lib.store import Store
        self.vlastni = tempfile.mkdtemp()
        self.spolecne = Store(tempfile.mkdtemp())
        self.engine = Engine({"hs_enabled": "true", "tmdb_api_key": "k"}, self.vlastni,
                             shared_store=self.spolecne)

    def _soubory(self, store):
        return set(pathlib.Path(store.dir, "cache").glob("*.json"))

    def test_bez_shared_store_je_spolecne_totez_co_vlastni(self):
        engine = Engine({}, tempfile.mkdtemp())
        self.assertIs(engine.shared, engine.store)

    def test_hellspy_tmdb_cinemeta_cachuji_do_spolecneho(self):
        self.assertIs(self.engine.hs.cache, self.spolecne)
        self.assertIs(self.engine.tmdb.cache, self.spolecne)
        self.assertIs(self.engine.cinemeta.cache, self.spolecne)
        self.assertIs(self.engine.sosac_db.cache, self.spolecne)

    def test_hlavicka_webshare_do_spolecneho_prehrajto_do_vlastniho(self):
        from nokturno_core import engine as engine_mod
        info = {"audio": ["cs"], "height": 1080, "size": 1}
        puvodni = engine_mod.probe_media
        engine_mod.probe_media = lambda url: info
        self.engine.resolve = lambda url, prefer_external=False: "http://x/" + url
        try:
            self.engine._media_from_file("ws:abc")
            self.assertEqual(len(self._soubory(self.spolecne)), 1)
            self.assertEqual(len(self._soubory(self.engine.store)), 0)
            self.engine._media_from_file("pt:1:slug:hash")
            self.engine._media_from_file("st:77")
            self.engine._media_from_file("dav:0:/film.mkv")
            self.assertEqual(len(self._soubory(self.spolecne)), 1)
            self.assertEqual(len(self._soubory(self.engine.store)), 3)
        finally:
            engine_mod.probe_media = puvodni

    def test_druhe_jadro_cte_hlavicku_z_cache_prvniho(self):
        from nokturno_core import engine as engine_mod
        volani = []

        def probe(url):
            volani.append(url)
            return {"audio": ["cs"], "height": 720, "size": 5}
        puvodni = engine_mod.probe_media
        engine_mod.probe_media = probe
        try:
            druhe = Engine({}, tempfile.mkdtemp(), shared_store=self.spolecne)
            for e in (self.engine, druhe):
                e.resolve = lambda url, prefer_external=False: "http://x/" + url
            self.assertEqual(self.engine._media_from_file("ws:abc")["height"], 720)
            self.assertEqual(druhe._media_from_file("ws:abc")["height"], 720)
            self.assertEqual(len(volani), 1)
        finally:
            engine_mod.probe_media = puvodni


class TestZamekPerKlicCache(unittest.TestCase):
    """`Store.cached_if()`: souběh nad týmž klíčem stáhne jednou, různé klíče se neblokují."""

    def test_soubezne_dotazy_na_tyz_klic_stahnou_jednou(self):
        import threading
        from nokturno_core.lib.store import Store
        store = Store(tempfile.mkdtemp())
        volani, brana = [], threading.Event()

        def loader():
            volani.append(1)
            brana.wait(2)
            return {"ok": 1}
        vysledky = []
        vlakna = [threading.Thread(target=lambda: vysledky.append(store.cached("k", 60, loader)))
                  for _ in range(8)]
        for t in vlakna:
            t.start()
        time.sleep(0.2)
        brana.set()
        for t in vlakna:
            t.join(5)
        self.assertEqual(len(volani), 1)
        self.assertEqual(vysledky, [{"ok": 1}] * 8)
        self.assertEqual(store._key_locks, {})   # po doběhnutí se zámek uklidí

    def test_ruzne_klice_se_neblokuji(self):
        import threading
        from nokturno_core.lib.store import Store
        store = Store(tempfile.mkdtemp())
        brana = threading.Event()
        hotovo = []

        def pomaly():
            brana.wait(2)
            return {"a": 1}
        t = threading.Thread(target=lambda: store.cached("pomaly", 60, pomaly))
        t.start()
        time.sleep(0.05)
        zacatek = time.time()
        self.assertEqual(store.cached("rychly", 60, lambda: {"b": 2}), {"b": 2})
        self.assertLess(time.time() - zacatek, 0.5)
        brana.set()
        t.join(5)

    def test_vyjimka_loaderu_uvolni_zamek(self):
        from nokturno_core.lib.store import Store
        store = Store(tempfile.mkdtemp())

        def spadne():
            raise ValueError("x")
        with self.assertRaises(ValueError):
            store.cached("k", 60, spadne)
        self.assertEqual(store._key_locks, {})
        self.assertEqual(store.cached("k", 60, lambda: 7), 7)


class TestHellspy429Text(unittest.TestCase):
    """HTTP 429 od HellSpy není překročený limit dotazů, ale blokace sítě uživatele
    (měřeno 2026-09-21: 76 hledání/s z čisté IP bez jediné 429; uživatelé s 429 ji mají
    od prvního dotazu). Hláška to má říct, ne psát holé „HTTP 429"."""

    def test_429_od_hellspy_rika_ze_odmita_sit(self):
        from nokturno_core.lib.hellspy_api import HellspyRateLimited
        from nokturno_core.lib.source_errors import describe_failure
        text = describe_failure("HellSpy", HellspyRateLimited("HTTP 429"))
        self.assertIn("odmítá tuto síť", text)
        self.assertIn("VPN", text)
        self.assertNotIn("HTTP 429:", text)
        pauza = HellspyRateLimited("HTTP 429 (pauza)")
        pauza.paused = True
        self.assertEqual(describe_failure("HellSpy", pauza), text)

    def test_429_jineho_zdroje_zustava_obecne(self):
        from nokturno_core.lib.source_errors import describe_failure
        self.assertEqual(describe_failure("Přehraj.to", "HTTP 429"), "Přehraj.to: HTTP 429")


class TestHlavickyZeServeru(unittest.TestCase):
    """`Engine._media_hints()`: hlavičky, které server zná, se nečtou ze souboru."""

    def _engine(self, **opts):
        from nokturno_core.lib.store import Store
        e = Engine({"media_hints": True, **opts}, tempfile.mkdtemp(), shared_store=Store(tempfile.mkdtemp()))
        e.resolve = lambda url, prefer_external=False: "http://x/" + url
        return e

    def test_trefa_ze_serveru_nahradi_cteni_souboru(self):
        from nokturno_core import engine as engine_mod
        e = self._engine()
        e.dash.media = lambda idents: {"ws:abc": {"audio": [{"lang": "cs"}], "height": 1080, "size": 5}}
        cteni = []
        puvodni = engine_mod.probe_media
        engine_mod.probe_media = lambda url: cteni.append(url) or {"audio": [{"lang": "en"}], "height": 720, "size": 1}
        try:
            e._media_hints(["ws:abc", "ws:xyz", "pt:1:s:h", None])
            self.assertEqual(e._media_from_file("ws:abc")["height"], 1080)   # ze serveru
            self.assertEqual(e._media_from_file("ws:xyz")["height"], 720)    # přečteno
            self.assertEqual(cteni, ["http://x/ws:xyz"])
            self.assertIn("hlavičky ze serveru", e.last_timings)
        finally:
            engine_mod.probe_media = puvodni

    def test_bez_volby_se_server_nepta(self):
        e = self._engine(media_hints=False)
        volani = []
        e.dash.media = lambda idents: volani.append(idents) or {}
        e._media_hints(["ws:abc"])
        self.assertEqual(volani, [])

    def test_pta_se_jen_na_sdilene_a_nezname(self):
        e = self._engine()
        e.shared.cached_if("media:ws:znamy", 3600, lambda: {"height": 480}, fresh=True)
        volani = []
        e.dash.media = lambda idents: volani.append(list(idents)) or {}
        e._media_hints(["ws:znamy", "ws:novy", "pt:1:s:h", "dav:0:/x", "ws:novy"])
        self.assertEqual(volani, [["ws:novy"]])


class TestZamekCekajiciDostanouNeuspech(unittest.TestCase):
    """Čekající nad týmž klíčem dostanou výsledek prvního, i když neprošel `ok()` —
    jinak by po neúspěchu spouštěli loader jeden po druhém (5 × timeout místo 1×)."""

    def test_neuspech_se_nestahuje_znovu_pro_cekajici(self):
        import threading
        from nokturno_core.lib.store import Store
        store = Store(tempfile.mkdtemp())
        volani, brana = [], threading.Event()

        def loader():
            volani.append(1)
            brana.wait(2)
            return {"ok": False}
        vysledky = []
        vlakna = [threading.Thread(target=lambda: vysledky.append(
            store.cached_if("k", 60, loader, ok=lambda d: d.get("ok")))) for _ in range(5)]
        for t in vlakna:
            t.start()
        time.sleep(0.2)
        brana.set()
        for t in vlakna:
            t.join(5)
        self.assertEqual(len(volani), 1)
        self.assertEqual(vysledky, [{"ok": False}] * 5)
        # po rozchodu všech se neúspěch nedrží: další volání zkusí znovu
        self.assertEqual(store.cached_if("k", 60, lambda: {"ok": True}, ok=lambda d: d.get("ok")), {"ok": True})
        self.assertEqual(len(volani), 1)
