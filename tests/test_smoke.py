"""Kontrola, že jádro drží, co na něm stojí ostatní.

Bez externích závislostí, aby šlo pustit kdekoli:

    python3 -m unittest discover -s tests -v

Nesahá na síť. Ověřuje jen tvar jádra a to, co na něm konzumenti vyžadují —
tedy věci, které se při rozesílání dají tiše rozbít.
"""
import ast
import inspect
import pathlib
import sys
import tempfile
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
                              "hellspy": False, "torrent": False})

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


if __name__ == "__main__":
    unittest.main()
