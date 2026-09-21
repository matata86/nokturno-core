"""Přehraj.to — čtení stránek a zapojení do jádra, nad HTML zachyceným z živého
serveru (2026-09-21, zkrácené). Bez sítě: `PrehrajtoApi._page` a `_open` nahrazuje
slovník cest.

    python3 -m unittest tests.test_prehrajto_api -v
"""
import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core import Engine                                            # noqa: E402
from nokturno_core.engine import NokturnoError                             # noqa: E402
from nokturno_core.lib import accounts as accounts_lib                     # noqa: E402
from nokturno_core.lib.prehrajto_api import (PER_PAGE, PrehrajtoApi,       # noqa: E402
                                             PrehrajtoError, PrehrajtoRateLimited,
                                             make_ref, parse_listing, parse_ref,
                                             parse_sub_ref, parse_tracks, sub_refs)
import nokturno_core.lib.prehrajto_api as pt_api                           # noqa: E402


def polozka(vid, slug, hash_, title, cas="02:10:46", kvalita="HD", velikost="2.68 GB",
            size_alone=False):
    """Jedna položka výpisu ve tvaru, v jakém ji server posílá."""
    trida = "video__tag video__tag--size video__tag--size-alone" if size_alone \
        else "video__tag video__tag--size"
    return f'''<div class="video__picture--container" data-video-id="{vid}">
<a class="video video--small video--link"
href="/{slug}/{hash_}"
title="{title}">
<div class="video__header">
<div class="video__tag video__tag--time">{cas}</div>
<div class="video__tag video__tag--format">
<span class="format__text">{kvalita}</span>
</div>
<div class="video__tag--others">
<div class="video__tag--likes"></div>
<div class="{trida}">
{velikost}
</div>
</div>
</div>
<h3 class="video__title margin-bottom-0">{title}</h3>
</a>'''


# „size-alone" je schválně: u videa bez hlasů přidává server ke třídě další a vzor,
# který počítal s holým `video__tag--size`, velikost minul u poloviny souborů
VYPIS = "<div class=\"video-listing\">" + "".join([
    polozka("15059509", "matrix-1999", "66103820811ee", "Matrix (1999)"),
    polozka("29559098", "matrix-1-1999-cz-dabing-5-1", "7cbc03e3ca5e850e",
            "Matrix 1 (1999) CZ Dabing 5.1 h264 HQ+ AVC 1080p",
            cas="02:16:17", velikost="13.08 GB", size_alone=True),
    polozka("22557303", "matrix-1-1999-4k", "89d453af0b2a71d2",
            "Matrix 1 (1999) CZ Dabing Ultra HD 2160p 4k", velikost="6.02 GB"),
]) + "</div>"

#: Stránka videa. Tytéž soubory posílá server dvakrát — pro jwplayer (`file:`)
#: a pro videojs (`src:`) — a podpisy v adresách se mezi bloky liší.
CDN = "https://pp-storage5.premiumcdn.net/164950826/"
VIDEO = f'''
<script>
var videos = [];
videos.push({{ src: "{CDN}aaa.mp4?token=T&expires=1790081334&signature=x", type: 'video/mp4', res: '1080', label: '1080p', default: true }});
videos.push({{ src: "{CDN}bbb.mp4?token=T&expires=1790081334&signature=y", type: 'video/mp4', res: '720', label: '720p' }});
var tracks = [
{{ file: "{CDN}cze.vtt?token=T&signature=1",  "default": true ,  label: "CZE - 8138711 - cze", kind: "captions" }},
{{ file: "{CDN}eng.vtt?token=T&signature=2",  label: "ENG - 8138712 - eng", kind: "captions" }}
];
var tracks = [
{{
src: "{CDN}cze.vtt?token=T&signature=9",
srclang: "cze",
label: "CZE - 8138711 - cze",
kind: "captions"
}},
{{
src: "{CDN}eng.vtt?token=T&signature=8",
srclang: "eng",
label: "ENG - 8138712 - eng"
}}
];
</script>'''

VIDEO_BEZ_TITULKU = f'''<script>
var videos = [];
videos.push({{ src: "{CDN}ccc.mp4?token=T", type: 'video/mp4', res: '1080', label: '1080p' }});
var tracks = [
];
</script>'''

PROFIL_PREMIUM = '''<div class="user">
<span class="premium">PREMIUM </span><span>42 dní</span>
<a href="/odhlasit">Odhlásit se</a></div>'''
PROFIL_BEZ_PREMIUM = '<div class="user"><a href="/odhlasit">Odhlásit se</a></div>'


class FakeApi(PrehrajtoApi):
    """Klient bez sítě: `_page` čte ze slovníku, `?do=download` vrací přesměrování."""

    def __init__(self, stranky, download=None, **kw):
        super().__init__(kw.pop("email", ""), kw.pop("password", ""), **kw)
        self.stranky = stranky
        self.download = download or {}
        self.dotazy = []
        self.loginy = 0

    def _page(self, path, **kw):
        zbyva = pt_api.blocked_for(self.cache)
        if zbyva:
            err = PrehrajtoRateLimited("pauza", status=429)
            err.paused = True
            raise err
        # `parse_ref` skládá celou adresu, hledání jen cestu — klíčem je vždy cesta
        path = path[len(pt_api.BASE):] if path.startswith(pt_api.BASE) else path
        self.dotazy.append(path)
        if path not in self.stranky:
            raise PrehrajtoError("HTTP 404", status=404)
        odpoved = self.stranky[path]
        if isinstance(odpoved, Exception):
            raise odpoved
        return odpoved

    def login(self):
        self.loginy += 1
        self._cookies = {"access_token": "t"}
        self._save_cookies(self._cookies)
        return self._cookies

    def _download_link(self, page_url):
        if not (self.email and self.password):
            return ""
        self.session()
        return self.download.get(page_url, "")


class TestCteniVypisu(unittest.TestCase):
    def test_polozky_vcetne_velikosti_u_tridy_navic(self):
        polozky = parse_listing(VYPIS)
        self.assertEqual(len(polozky), 3)
        prvni = polozky[0]
        self.assertEqual(prvni["name"], "Matrix (1999)")
        self.assertEqual(prvni["slug"], "matrix-1999")
        self.assertEqual(prvni["hash"], "66103820811ee")
        self.assertEqual(prvni["duration"], 2 * 3600 + 10 * 60 + 46)
        self.assertTrue(prvni["hd"])
        # velikost je z původního souboru, ne z překódování
        self.assertEqual(prvni["size"], int(2.68 * 1024 ** 3))
        # „video__tag--size-alone": třída navíc nesmí velikost shodit
        self.assertEqual(polozky[1]["size"], int(13.08 * 1024 ** 3))
        self.assertTrue(all(p["size_h"] for p in polozky))

    def test_prazdny_vypis(self):
        self.assertEqual(parse_listing("<div class=\"video-listing\"></div>"), [])

    def test_titulek_s_entitou(self):
        page = polozka("1", "a-b", "abcdef1234", "Kluci &amp; holky")
        self.assertEqual(parse_listing(page)[0]["name"], "Kluci & holky")


class TestOdkazy(unittest.TestCase):
    def test_tam_a_zpet(self):
        ref = make_ref({"slug": "matrix-1999", "hash": "66103820811ee"})
        self.assertEqual(ref, "pt:matrix-1999:66103820811ee")
        self.assertEqual(parse_ref(ref), "https://prehraj.to/matrix-1999/66103820811ee")

    def test_odkaz_z_ciziho_tvaru_neprojde(self):
        # odkaz posílá i klient (Stremio `/play/`) — bez kontroly by cesta mohla vést kamkoli
        for zly in ["pt:../../etc:66103820811ee", "pt:a/b:66103820811ee", "pt:a:xyz",
                    "pt:a:", "ws:123", "", None, "pt:a:66103820811ee?x=1"]:
            with self.assertRaises(PrehrajtoError):
                parse_ref(zly)

    def test_odkaz_na_titulky(self):
        self.assertEqual(sub_refs("pt:a-b:66103820811ee", 2),
                         ["pts:a-b:66103820811ee:0", "pts:a-b:66103820811ee:1"])
        self.assertEqual(parse_sub_ref("pts:a-b:66103820811ee:1"),
                         ("https://prehraj.to/a-b/66103820811ee", 1))
        with self.assertRaises(PrehrajtoError):
            parse_sub_ref("pts:a-b:66103820811ee:x")


class TestTitulky(unittest.TestCase):
    def test_oba_tvary_bloku_a_bez_duplicit(self):
        """Server posílá tentýž seznam dvakrát (jwplayer `file:`, videojs `src:`)
        a podpisy se liší — počítat se smí každý soubor jen jednou."""
        found = parse_tracks(VIDEO)
        self.assertEqual([lang for _url, lang in found], ["CZE", "ENG"])
        self.assertTrue(found[0][0].endswith("signature=1"))

    def test_stranka_bez_titulku(self):
        self.assertEqual(parse_tracks(VIDEO_BEZ_TITULKU), [])


class TestPrehravani(unittest.TestCase):
    URL = "https://prehraj.to/matrix-1999/66103820811ee"
    CESTA = "/matrix-1999/66103820811ee"
    REF = "pt:matrix-1999:66103820811ee"

    def test_bez_uctu_se_vezme_nejlepsi_prekodovani(self):
        api = FakeApi({self.CESTA: VIDEO})
        self.assertEqual(api.file_link(self.REF), f"{CDN}aaa.mp4?token=T&expires=1790081334&signature=x")

    def test_s_premium_se_vezme_puvodni_soubor(self):
        """`?do=download` vydá originál — ten má jinou velikost i příponu než překódování."""
        api = FakeApi({self.CESTA: VIDEO}, download={self.URL: CDN + "puvodni.mkv?token=T"},
                      email="a@b.cz", password="x")
        self.assertEqual(api.file_link(self.REF), CDN + "puvodni.mkv?token=T")
        # stránka videa se pak vůbec nečte
        self.assertEqual(api.dotazy, [])

    def test_bez_premium_spadne_na_prekodovani(self):
        """Účet bez Premium na `?do=download` odkaz nedostane — server přesměruje
        zpátky na stránku videa, takže se musí vzít překódovaná verze."""
        api = FakeApi({self.CESTA: VIDEO}, download={}, email="a@b.cz", password="x")
        self.assertTrue(api.file_link(self.REF).endswith("signature=x"))

    def test_hlavicky_nejsou_potreba(self):
        api = FakeApi({self.CESTA: VIDEO})
        url, headers = api.request(self.REF)
        self.assertTrue(url)
        self.assertEqual(headers, {})

    def test_stranka_bez_souboru(self):
        api = FakeApi({self.CESTA: "<html>nic</html>"})
        with self.assertRaises(PrehrajtoError):
            api.file_link(self.REF)

    def test_titulky_podle_poradi(self):
        api = FakeApi({self.CESTA: VIDEO})
        self.assertTrue(api.subtitle_link("pts:matrix-1999:66103820811ee:1").endswith("signature=2"))
        with self.assertRaises(PrehrajtoError):
            api.subtitle_link("pts:matrix-1999:66103820811ee:5")


class TestHledani(unittest.TestCase):
    def test_bez_uctu_jen_prvni_strana(self):
        """Dál se server ptá na přihlášení, druhou stranu by stejně nevydal."""
        api = FakeApi({"/hledej/Matrix": VYPIS,
                       "/hledej/Matrix?videoListing-visualPaginator-page=2": VYPIS})
        files, total = api.search("Matrix", limit=64)
        self.assertEqual(total, 3)
        self.assertEqual(api.dotazy, ["/hledej/Matrix"])

    def test_s_uctem_se_strankuje(self):
        druha = "".join(polozka(str(900 + i), f"film-{i}", f"aa00bb11cc{i:02d}", f"Film {i}")
                        for i in range(PER_PAGE))
        prvni = "".join(polozka(str(i), f"matrix-{i}", f"ff00aa11bb{i:02d}", f"Matrix {i}")
                        for i in range(PER_PAGE))
        api = FakeApi({"/hledej/Matrix": prvni,
                       "/hledej/Matrix?videoListing-visualPaginator-page=2": druha},
                      email="a@b.cz", password="x")
        files, total = api.search("Matrix", limit=64)
        self.assertEqual(total, 2 * PER_PAGE)
        self.assertEqual(len(api.dotazy), 2)

    def test_strankovani_skonci_na_kratsi_strane(self):
        api = FakeApi({"/hledej/Matrix": VYPIS}, email="a@b.cz", password="x")
        files, _total = api.search("Matrix", limit=64)
        self.assertEqual(len(files), 3)
        self.assertEqual(len(api.dotazy), 1)

    def test_strankovani_skonci_kdyz_strana_nic_noveho_neprinese(self):
        """Server na některé dotazy vrací pořád tutéž stranu — bez téhle pojistky
        by se `MAX_PAGES` dotazů protočilo naprázdno."""
        stejna = {"/hledej/Matrix": VYPIS}
        stejna.update({f"/hledej/Matrix?videoListing-visualPaginator-page={n}": VYPIS
                       for n in range(2, 6)})
        api = FakeApi(stejna, email="a@b.cz", password="x")
        # strana je plná, takže o zastavení rozhoduje jen „nic nového"
        api.stranky["/hledej/Matrix"] = "".join(
            polozka(str(i), f"m-{i}", f"ff00aa11bb{i:02d}", f"Matrix {i}") for i in range(PER_PAGE))
        api.stranky["/hledej/Matrix?videoListing-visualPaginator-page=2"] = api.stranky["/hledej/Matrix"]
        files, _total = api.search("Matrix", limit=200)
        self.assertEqual(len(files), PER_PAGE)
        self.assertEqual(len(api.dotazy), 2)

    def test_prazdny_dotaz_se_neptá(self):
        api = FakeApi({})
        self.assertEqual(api.search("   "), ([], 0))
        self.assertEqual(api.dotazy, [])


class TestPauzaPo429(unittest.TestCase):
    def setUp(self):
        pt_api._blocked_until = 0.0

    tearDown = setUp

    def test_pauza_plati_i_pro_dalsi_proces(self):
        """V Kodi je plugin jiný interpret než služba — pauza musí být na disku,
        jinak by o ní druhý proces nevěděl a blokaci prodlužoval."""
        with tempfile.TemporaryDirectory() as tmp:
            from nokturno_core.lib.store import Store
            store = Store(tmp)
            self.assertEqual(pt_api.blocked_for(store), 0.0)
            pt_api._note_block(store)
            pt_api._blocked_until = 0.0            # jiný proces paměť nemá
            self.assertGreater(pt_api.blocked_for(store), 0)

    def test_behem_pauzy_se_nehleda(self):
        api = FakeApi({"/hledej/Matrix": VYPIS})
        pt_api._note_block(None)
        with self.assertRaises(PrehrajtoRateLimited):
            api.search("Matrix")
        self.assertEqual(api.dotazy, [])


class TestStavUctu(unittest.TestCase):
    def setUp(self):
        pt_api._blocked_until = 0.0

    tearDown = setUp

    def test_bez_uctu_je_zdroj_v_poradku(self):
        """Bez účtu zdroj funguje, jen s méně výsledky — to není chyba uživatele."""
        zaznam = accounts_lib.prehrajto(FakeApi({}))
        self.assertEqual((zaznam["level"], zaznam["code"]), (accounts_lib.OK, "anonymous"))

    def test_premium_i_se_zbyvajicimi_dny(self):
        api = FakeApi({"/profil": PROFIL_PREMIUM}, email="a@b.cz", password="x")
        zaznam = accounts_lib.prehrajto(api)
        self.assertEqual((zaznam["level"], zaznam["code"]), (accounts_lib.OK, "premium"))
        self.assertEqual(zaznam["detail"]["days"], 42)

    def test_ucet_bez_premium_je_varovani(self):
        api = FakeApi({"/profil": PROFIL_BEZ_PREMIUM}, email="a@b.cz", password="x")
        zaznam = accounts_lib.prehrajto(api)
        self.assertEqual((zaznam["level"], zaznam["code"]), (accounts_lib.WARN, "no_premium"))

    def test_konec_predplatneho_se_ozve_predem(self):
        strana = PROFIL_PREMIUM.replace("42 dní", "3 dní")
        api = FakeApi({"/profil": strana}, email="a@b.cz", password="x")
        zaznam = accounts_lib.prehrajto(api)
        self.assertEqual((zaznam["level"], zaznam["code"]), (accounts_lib.WARN, "expires_soon"))

    def test_pauza_se_hlasi_bez_dotazu_na_sit(self):
        api = FakeApi({}, email="a@b.cz", password="x")
        pt_api._note_block(None)
        zaznam = accounts_lib.prehrajto(api)
        self.assertEqual((zaznam["level"], zaznam["code"]), (accounts_lib.WARN, "paused"))
        self.assertEqual(api.dotazy, [])


class TestPrihlaseni(unittest.TestCase):
    def test_cookies_se_pamatuji(self):
        """Každé přihlášení zakládá na serveru další „přihlášené zařízení" —
        proto se relace ukládá a nepřihlašuje se při každém dotazu znovu."""
        with tempfile.TemporaryDirectory() as tmp:
            from nokturno_core.lib.store import Store
            store = Store(tmp)
            api = FakeApi({"/profil": PROFIL_PREMIUM}, email="a@b.cz", password="x", cache=store)
            api.session()
            api.session()
            self.assertEqual(api.loginy, 1)
            druhy = FakeApi({"/profil": PROFIL_PREMIUM}, email="a@b.cz", password="x", cache=store)
            druhy.session()
            self.assertEqual(druhy.loginy, 0)      # relaci převzal z úložiště

    def test_jiny_ucet_cizi_relaci_nepouzije(self):
        with tempfile.TemporaryDirectory() as tmp:
            from nokturno_core.lib.store import Store
            store = Store(tmp)
            FakeApi({}, email="a@b.cz", password="x", cache=store).session()
            jiny = FakeApi({}, email="c@d.cz", password="y", cache=store)
            jiny.session()
            self.assertEqual(jiny.loginy, 1)

    def test_forget_zahodi_i_ulozenou_relaci(self):
        with tempfile.TemporaryDirectory() as tmp:
            from nokturno_core.lib.store import Store
            store = Store(tmp)
            api = FakeApi({}, email="a@b.cz", password="x", cache=store)
            api.session()
            api.forget()
            api.session()
            self.assertEqual(api.loginy, 2)


class TestZapojeniDoJadra(unittest.TestCase):
    def engine(self, **opts):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        return Engine({"pt_enabled": True, **opts}, self.tmp.name)

    def test_vypnuty_zdroj_se_neptá(self):
        engine = Engine({}, tempfile.mkdtemp())
        self.assertIsNone(engine.pt)
        self.assertFalse(engine.sources()["prehrajto"])
        self.assertEqual(engine._prehrajto_streams({"name": "Matrix"}), [])

    def test_zapnuty_zdroj_bez_uctu(self):
        engine = self.engine()
        self.assertIsNotNone(engine.pt)
        self.assertTrue(engine.sources()["prehrajto"])

    def test_streamy_prochazi_filtrem_nazvu(self):
        """Fulltext vrací i cizí tituly — projít smí jen ten hledaný."""
        engine = self.engine()
        engine._pt = FakeApi({"/hledej/Matrix%201999": VYPIS + polozka(
            "77", "vikingove-1999", "aa11bb22cc33", "Vikingové 1999 CZ 1080p")})
        streams = engine._prehrajto_streams({"name": "Matrix", "year": "1999"}, None, "movie")
        self.assertTrue(streams)
        self.assertTrue(all("Matrix" in s["label"] for s in streams))
        self.assertTrue(all(s["url"].startswith("pt:") and s["source"] == "pt" for s in streams))

    def test_velikost_se_ukaze_jen_s_uctem(self):
        """Velikost z výpisu patří původnímu souboru — ten dostane jen Premium.
        Bez účtu se hraje překódování, takže by popisek lhal."""
        engine = self.engine()
        engine._pt = FakeApi({"/hledej/Matrix%201999": VYPIS})
        self.assertTrue(all(not s["detail"] for s in
                            engine._prehrajto_streams({"name": "Matrix", "year": "1999"}, None, "movie")))
        engine = self.engine(pt_email="a@b.cz", pt_password="x")
        engine._pt = FakeApi({"/hledej/Matrix%201999": VYPIS}, email="a@b.cz", password="x")
        self.assertTrue(all(s["detail"] for s in
                            engine._prehrajto_streams({"name": "Matrix", "year": "1999"}, None, "movie")))

    def test_429_prerusi_dalsi_dotazy(self):
        """Po první 429 nemá smysl zkoušet další varianty názvu — blokaci by to
        jen prodloužilo (táž chyba jako u HellSpy v 6.0.2 a 6.0.4)."""
        pt_api._blocked_until = 0.0
        self.addCleanup(setattr, pt_api, "_blocked_until", 0.0)
        engine = self.engine()

        class Omezeny(FakeApi):
            def _page(self, path, **kw):
                self.dotazy.append(path)
                raise PrehrajtoRateLimited("HTTP 429", status=429)

        engine._pt = Omezeny({})
        failures = []
        self.assertEqual(engine._prehrajto_streams({"name": "Matrix", "year": "1999"},
                                                   None, "movie", failures=failures), [])
        self.assertEqual(len(engine._pt.dotazy), 1)
        self.assertEqual(failures[0][0], "Přehraj.to")

    def test_vypadek_jednoho_dotazu_hledani_neshodi(self):
        engine = self.engine()
        engine._pt = FakeApi({})          # každá cesta je 404
        failures = []
        self.assertEqual(engine._prehrajto_streams({"name": "Matrix", "year": "1999"},
                                                   None, "movie", failures=failures), [])
        self.assertTrue(failures)

    def test_resolve_odkazu_i_titulku(self):
        engine = self.engine()
        engine._pt = FakeApi({"/matrix-1999/66103820811ee": VIDEO})
        self.assertTrue(engine.resolve("pt:matrix-1999:66103820811ee").endswith("signature=x"))
        self.assertTrue(engine.resolve("pts:matrix-1999:66103820811ee:0").endswith("signature=1"))

    def test_resolve_s_vypnutym_zdrojem(self):
        engine = Engine({}, tempfile.mkdtemp())
        for odkaz in ("pt:matrix-1999:66103820811ee", "pts:matrix-1999:66103820811ee:0"):
            with self.assertRaises(NokturnoError):
                engine.resolve(odkaz)

    def test_ucet_meni_klic_cache_streamu(self):
        """Bez účtu je vidět jen první strana a hraje se překódování — po přidání
        účtu se výsledek liší, takže starý seznam nesmí zůstat platný 72 h."""
        bez = self.engine()._streams_cache_key("movie", "tt0133093", None)
        s_uctem = self.engine(pt_email="a@b.cz", pt_password="x")._streams_cache_key(
            "movie", "tt0133093", None)
        vypnuty = Engine({}, tempfile.mkdtemp())._streams_cache_key("movie", "tt0133093", None)
        self.assertNotEqual(bez, s_uctem)
        self.assertNotEqual(bez, vypnuty)

    def test_hlavicka_souboru_se_cte(self):
        """`pt:` je podepsaný odkaz bez hlaviček a bez kreditu — zvuk a rozlišení
        se z něj smí číst vždy, na rozdíl od FastShare."""
        engine = self.engine()
        engine._pt = FakeApi({"/matrix-1999/66103820811ee": VIDEO})
        cteno = []
        engine._media_from_file = lambda url: cteno.append(url) or {}
        engine._fill_audio([{"url": "pt:matrix-1999:66103820811ee", "label": "Matrix"}])
        self.assertEqual(cteno, ["pt:matrix-1999:66103820811ee"])


if __name__ == "__main__":
    unittest.main()
