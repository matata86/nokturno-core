"""Přehraj.to — JSON API s účtem, HTML bez účtu, a zapojení do jádra.

Bez sítě: účet čte `PrehrajtoApi._api` (JSON), bez účtu `_page` (HTML) — obojí
nahrazuje slovník v `FakeApi`.

    python3 -m unittest tests.test_prehrajto_api -v
"""
import pathlib
import sys
import tempfile
import threading
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


# --- HTML (cesta bez účtu) --------------------------------------------------

def polozka(vid, slug, hash_, title, cas="02:10:46", kvalita="HD", velikost="2.68 GB",
            size_alone=False):
    """Jedna položka HTML výpisu ve tvaru, v jakém ji server posílá."""
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


VYPIS = "<div class=\"video-listing\">" + "".join([
    polozka("15059509", "matrix-1999", "66103820811ee", "Matrix (1999)"),
    polozka("29559098", "matrix-1-1999-cz-dabing-5-1", "7cbc03e3ca5e850e",
            "Matrix 1 (1999) CZ Dabing 5.1 h264 HQ+ AVC 1080p",
            cas="02:16:17", velikost="13.08 GB", size_alone=True),
    polozka("22557303", "matrix-1-1999-4k", "89d453af0b2a71d2",
            "Matrix 1 (1999) CZ Dabing Ultra HD 2160p 4k", velikost="6.02 GB"),
]) + "</div>"

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
</script>'''

VIDEO_BEZ_SOUBORU = "<html>nic</html>"


# --- JSON API (cesta s účtem) ----------------------------------------------

def raw(vid, slug, hash_, name, size="2874045184", length=7846, hd=True, subs=None):
    """Jedna položka JSON API `videos/search`, jak ji server posílá."""
    it = {"id": vid, "slug": slug, "hash": hash_, "name": name,
          "size": size, "length": length, "hd": hd}
    if subs is not None:
        it["subtitles"] = subs
    return it


VYSLEDKY = [
    raw(15059509, "matrix-1999", "66103820811ee", "Matrix (1999)"),
    raw(22557303, "matrix-1-1999-4k", "89d453af0b2a71d2",
        "Matrix 1 (1999) CZ Dabing Ultra HD 2160p 4k", size="6499439616",
        subs=[{"cdnUrl": CDN + "cze.vtt?token=A&signature=1", "language": "cze"},
              {"cdnUrl": CDN + "eng.vtt?token=A&signature=2", "language": "eng"}]),
]


class FakeApi(PrehrajtoApi):
    """Klient bez sítě. `_api` čte z `vysledky`/`download`/`subs`, `_page` z `stranky`."""

    def __init__(self, stranky=None, vysledky=None, download=None, subs=None,
                 html_download=None, **kw):
        super().__init__(kw.pop("email", ""), kw.pop("password", ""), **kw)
        self.stranky = stranky or {}
        self.vysledky = vysledky if vysledky is not None else []
        self.download = download or {}          # vid → odkaz (JSON)
        self.subs = subs or {}                  # vid → [{cdnUrl, language}]
        self.html_download = html_download or {}  # adresa stránky → odkaz (HTML fallback)
        self.dotazy = []            # HTML cesty
        self.api_dotazy = []        # (path, params) JSON
        self.loginy = 0

    # HTML
    def _page(self, path, **kw):
        self._paused()
        path = path[len(pt_api.BASE):] if path.startswith(pt_api.BASE) else path
        self.dotazy.append(path)
        if path not in self.stranky:
            raise PrehrajtoError("HTTP 404", status=404)
        odpoved = self.stranky[path]
        if isinstance(odpoved, Exception):
            raise odpoved
        return odpoved

    # JSON
    def _api(self, path, params=None):
        self._paused()
        self.api_dotazy.append((path, params))
        if path == "videos/search":
            v = self.vysledky
            if isinstance(v, dict):
                v = v.get(params.get("phrase"), [])
            off, lim = int(params.get("offset", 0)), int(params.get("limit", PER_PAGE))
            data = v[off:off + lim]
            return {"count": len(data), "data": data}
        if path.endswith("/download"):
            vid = path.split("/")[1]
            return {"count": 1, "data": {"download_link": self.download.get(vid, "")}}
        vid = path.split("/")[1]                # videos/{id}
        return {"count": 1, "data": {"subtitles": self.subs.get(vid, [])}}

    def _bearer(self, force=False):
        self.session()
        return "t"

    def login(self):
        self.loginy += 1
        self._cookies = {"access_token": "t"}
        self._save_cookies(self._cookies)
        return self._cookies

    def _html_download(self, page_url):
        if not self._account:
            return ""
        self.session()
        return self.html_download.get(page_url, "")


# --- čtení výpisu a odkazů --------------------------------------------------

class TestCteniVypisuHtml(unittest.TestCase):
    def test_polozky_vcetne_velikosti_u_tridy_navic(self):
        polozky = parse_listing(VYPIS)
        self.assertEqual(len(polozky), 3)
        prvni = polozky[0]
        self.assertEqual(prvni["name"], "Matrix (1999)")
        self.assertEqual(prvni["slug"], "matrix-1999")
        self.assertEqual(prvni["hash"], "66103820811ee")
        self.assertEqual(prvni["id"], "")       # HTML výpis id nenese
        self.assertEqual(prvni["duration"], 2 * 3600 + 10 * 60 + 46)
        self.assertTrue(prvni["hd"])
        self.assertEqual(prvni["size"], int(2.68 * 1024 ** 3))
        self.assertEqual(polozky[1]["size"], int(13.08 * 1024 ** 3))
        self.assertTrue(all(p["size_h"] for p in polozky))

    def test_prazdny_vypis(self):
        self.assertEqual(parse_listing("<div class=\"video-listing\"></div>"), [])

    def test_titulek_s_entitou(self):
        page = polozka("1", "a-b", "abcdef1234", "Kluci &amp; holky")
        self.assertEqual(parse_listing(page)[0]["name"], "Kluci & holky")


class TestMapovaniJson(unittest.TestCase):
    def test_polozka_api_na_tvar_jadra(self):
        m = PrehrajtoApi._map_item(raw(15059509, "matrix-1999", "66103820811ee", "Matrix (1999)"))
        self.assertEqual(m["id"], "15059509")
        self.assertEqual(m["slug"], "matrix-1999")
        self.assertEqual(m["hash"], "66103820811ee")
        self.assertEqual(m["name"], "Matrix (1999)")
        self.assertEqual(m["size"], 2874045184)
        self.assertEqual(m["duration"], 7846)
        self.assertTrue(m["hd"])
        self.assertTrue(m["size_h"])


class TestOdkazy(unittest.TestCase):
    def test_tam_a_zpet_s_id(self):
        ref = make_ref({"id": "15059509", "slug": "matrix-1999", "hash": "66103820811ee"})
        self.assertEqual(ref, "pt:15059509:matrix-1999:66103820811ee")
        self.assertEqual(parse_ref(ref), "https://prehraj.to/matrix-1999/66103820811ee")

    def test_tam_a_zpet_bez_id_starsi_tvar(self):
        ref = make_ref({"slug": "matrix-1999", "hash": "66103820811ee"})
        self.assertEqual(ref, "pt:matrix-1999:66103820811ee")
        self.assertEqual(parse_ref(ref), "https://prehraj.to/matrix-1999/66103820811ee")

    def test_odkaz_z_ciziho_tvaru_neprojde(self):
        # odkaz posílá i klient (Stremio `/play/`) — bez kontroly by cesta mohla vést kamkoli
        for zly in ["pt:../../etc:66103820811ee", "pt:a/b:66103820811ee", "pt:a:xyz",
                    "pt:a:", "ws:123", "", None, "pt:a:66103820811ee?x=1"]:
            with self.assertRaises(PrehrajtoError):
                parse_ref(zly)

    def test_odkaz_na_titulky_s_id_i_bez(self):
        self.assertEqual(sub_refs("pt:15059509:a-b:66103820811ee", 2),
                         ["pts:15059509:a-b:66103820811ee:0", "pts:15059509:a-b:66103820811ee:1"])
        self.assertEqual(sub_refs("pt:a-b:66103820811ee", 2),
                         ["pts:a-b:66103820811ee:0", "pts:a-b:66103820811ee:1"])
        self.assertEqual(parse_sub_ref("pts:15059509:a-b:66103820811ee:1"),
                         ("https://prehraj.to/a-b/66103820811ee", 1))
        self.assertEqual(parse_sub_ref("pts:a-b:66103820811ee:1"),
                         ("https://prehraj.to/a-b/66103820811ee", 1))
        with self.assertRaises(PrehrajtoError):
            parse_sub_ref("pts:a-b:66103820811ee:x")


class TestTitulkyHtml(unittest.TestCase):
    def test_oba_tvary_bloku_a_bez_duplicit(self):
        found = parse_tracks(VIDEO)
        self.assertEqual([lang for _url, lang in found], ["CZE", "ENG"])
        self.assertTrue(found[0][0].endswith("signature=1"))

    def test_stranka_bez_titulku(self):
        self.assertEqual(parse_tracks("<script>var tracks = [];</script>"), [])


# --- hledání ----------------------------------------------------------------

class TestHledani(unittest.TestCase):
    def test_bez_uctu_jen_prvni_strana_html(self):
        """Bez účtu se čte HTML — server dál chce přihlášení, druhou stranu nevydá."""
        api = FakeApi({"/hledej/Matrix": VYPIS})
        files, total = api.search("Matrix", limit=64)
        self.assertEqual(total, 3)
        self.assertEqual(api.dotazy, ["/hledej/Matrix"])
        self.assertEqual(api.api_dotazy, [])    # JSON se bez účtu nevolá

    def test_s_uctem_jde_pres_json_a_strankuje(self):
        vysledky = [raw(i + 1, f"film-{i}", f"aa00bb11cc{i:04d}", f"Film {i}") for i in range(2 * PER_PAGE)]
        api = FakeApi(vysledky=vysledky, email="a@b.cz", password="x")
        files, total = api.search("Film", limit=64)
        self.assertEqual(total, 2 * PER_PAGE)
        self.assertEqual(len(api.api_dotazy), 2)               # dvě strany po offsetu
        self.assertEqual(api.dotazy, [])                       # HTML se s účtem nevolá
        self.assertEqual([p[1]["offset"] for p in api.api_dotazy], [0, PER_PAGE])
        self.assertTrue(all(f["id"] for f in files))           # id z JSON → download půjde přímo

    def test_strankovani_skonci_na_kratsi_strane(self):
        api = FakeApi(vysledky=[raw(i, f"m-{i}", f"ff00aa11bb{i:04d}", f"M {i}") for i in range(3)],
                      email="a@b.cz", password="x")
        files, _total = api.search("M", limit=64)
        self.assertEqual(len(files), 3)
        self.assertEqual(len(api.api_dotazy), 1)

    def test_strankovani_skonci_kdyz_strana_nic_noveho_neprinese(self):
        """Server na některé offsety vrací pořád tytéž hashe — bez téhle pojistky
        by se `MAX_PAGES` dotazů protočilo naprázdno."""
        jedna = [raw(i, f"m-{i}", f"ff00aa11bb{i:04d}", f"M {i}") for i in range(PER_PAGE)]

        class Stejna(FakeApi):
            def _api(self, path, params=None):
                self.api_dotazy.append((path, params))
                return {"count": len(jedna), "data": jedna}    # každý offset stejná strana

        api = Stejna(email="a@b.cz", password="x")
        files, _total = api.search("M", limit=200)
        self.assertEqual(len(files), PER_PAGE)
        self.assertEqual(len(api.api_dotazy), 2)               # druhá nepřinesla nic nového → stop

    def test_prazdny_dotaz_se_neptá(self):
        api = FakeApi()
        self.assertEqual(api.search("   "), ([], 0))
        self.assertEqual(api.dotazy, [])
        self.assertEqual(api.api_dotazy, [])


# --- přehrání ---------------------------------------------------------------

class TestPrehravaniJson(unittest.TestCase):
    REF = "pt:22557303:matrix-1-1999-4k:89d453af0b2a71d2"

    def test_s_uctem_original_z_json(self):
        api = FakeApi(download={"22557303": CDN + "puvodni.mkv?token=T"},
                      email="a@b.cz", password="x")
        self.assertEqual(api.file_link(self.REF), CDN + "puvodni.mkv?token=T")
        self.assertEqual(api.dotazy, [])                       # HTML se nečte

    def test_prazdny_odkaz_spadne_na_html_prekodovani(self):
        """Účet bez Premium na download odkaz nedostane — spadne na HTML stránku videa."""
        api = FakeApi({"/matrix-1-1999-4k/89d453af0b2a71d2": VIDEO},
                      download={"22557303": ""}, email="a@b.cz", password="x")
        self.assertTrue(api.file_link(self.REF).endswith("signature=x"))

    def test_titulky_z_json(self):
        api = FakeApi(subs={"22557303": [
            {"cdnUrl": CDN + "cze.vtt?s=1", "language": "cze"},
            {"cdnUrl": CDN + "eng.vtt?s=2", "language": "eng"}]},
            email="a@b.cz", password="x")
        self.assertEqual(api.tracks(self.REF), [(CDN + "cze.vtt?s=1", "CZE"), (CDN + "eng.vtt?s=2", "ENG")])
        self.assertEqual(api.subtitle_link("pts:22557303:matrix-1-1999-4k:89d453af0b2a71d2:1"),
                         CDN + "eng.vtt?s=2")

    def test_hlavicky_nejsou_potreba(self):
        api = FakeApi(download={"22557303": CDN + "x.mkv"}, email="a@b.cz", password="x")
        url, headers = api.request(self.REF)
        self.assertTrue(url)
        self.assertEqual(headers, {})


class TestPrehravaniHtml(unittest.TestCase):
    """Starší odkaz bez id (z HTML výpisu bez účtu) i anonymní přehrání jde přes HTML."""

    CESTA = "/matrix-1999/66103820811ee"
    REF = "pt:matrix-1999:66103820811ee"

    def test_bez_uctu_nejlepsi_prekodovani(self):
        api = FakeApi({self.CESTA: VIDEO})
        self.assertEqual(api.file_link(self.REF), f"{CDN}aaa.mp4?token=T&expires=1790081334&signature=x")
        self.assertEqual(api.api_dotazy, [])                   # bez id JSON nevoláme

    def test_starsi_odkaz_s_uctem_premium_pres_html_download(self):
        api = FakeApi({self.CESTA: VIDEO},
                      html_download={"https://prehraj.to/matrix-1999/66103820811ee": CDN + "orig.mkv"},
                      email="a@b.cz", password="x")
        self.assertEqual(api.file_link(self.REF), CDN + "orig.mkv")

    def test_stranka_bez_souboru(self):
        api = FakeApi({self.CESTA: VIDEO_BEZ_SOUBORU})
        with self.assertRaises(PrehrajtoError):
            api.file_link(self.REF)

    def test_titulky_podle_poradi_html(self):
        api = FakeApi({self.CESTA: VIDEO})
        self.assertTrue(api.subtitle_link("pts:matrix-1999:66103820811ee:1").endswith("signature=2"))
        with self.assertRaises(PrehrajtoError):
            api.subtitle_link("pts:matrix-1999:66103820811ee:5")


# --- pauza po 429 (společné oběma cestám) -----------------------------------

class TestPauzaPo429(unittest.TestCase):
    def setUp(self):
        pt_api._blocked_until = 0.0

    tearDown = setUp

    def test_pauza_plati_i_pro_dalsi_proces(self):
        with tempfile.TemporaryDirectory() as tmp:
            from nokturno_core.lib.store import Store
            store = Store(tmp)
            self.assertEqual(pt_api.blocked_for(store), 0.0)
            pt_api._note_block(store)
            pt_api._blocked_until = 0.0            # jiný proces paměť nemá
            self.assertGreater(pt_api.blocked_for(store), 0)

    def test_behem_pauzy_se_nehleda_html(self):
        api = FakeApi({"/hledej/Matrix": VYPIS})
        pt_api._note_block(None)
        with self.assertRaises(PrehrajtoRateLimited):
            api.search("Matrix")
        self.assertEqual(api.dotazy, [])

    def test_behem_pauzy_se_nehleda_json(self):
        api = FakeApi(vysledky=VYSLEDKY, email="a@b.cz", password="x")
        pt_api._note_block(None)
        with self.assertRaises(PrehrajtoRateLimited):
            api.search("Matrix")
        self.assertEqual(api.api_dotazy, [])


class TestFrontaDotazuHtml(unittest.TestCase):
    """Fronta `MIN_GAP` platí jen pro HTML (plovoucí 429 scrapingu); JSON API ji nemá."""

    def setUp(self):
        self._old = (pt_api.MIN_GAP, pt_api.MAX_QUEUE_WAIT, pt_api._next_slot)
        pt_api._next_slot = 0.0
        pt_api._blocked_until = 0.0

    def tearDown(self):
        pt_api.MIN_GAP, pt_api.MAX_QUEUE_WAIT, pt_api._next_slot = self._old
        pt_api._blocked_until = 0.0

    def test_html_dotazy_z_vice_vlaken_jdou_za_sebou(self):
        pt_api.MIN_GAP, pt_api.MAX_QUEUE_WAIT = 0.2, 5.0
        casy = []
        api = PrehrajtoApi("", "")
        api._open = lambda *a, **k: (casy.append(time.time()), _Odpoved())[1]
        vlakna = [threading.Thread(target=api._page, args=("/x",)) for _ in range(4)]
        for v in vlakna:
            v.start()
        for v in vlakna:
            v.join()
        casy.sort()
        self.assertEqual(len(casy), 4)
        for a, b in zip(casy, casy[1:]):
            self.assertGreaterEqual(b - a, 0.15)

    def test_json_frontou_nedrzi(self):
        """40 souběžných JSON dotazů projde bez čekání — ověřeno i živě na serveru."""
        pt_api.MIN_GAP = 10.0        # kdyby JSON frontu držel, tohle by ho zabrzdilo
        api = PrehrajtoApi("a@b.cz", "x")
        api._bearer = lambda force=False: "t"
        api._open = lambda *a, **k: _JsonOdpoved('{"status":"ok","payload":{"count":0,"data":[]}}')
        start = time.time()
        vlakna = [threading.Thread(target=lambda: api._api("videos/search", {"phrase": "x"}))
                  for _ in range(6)]
        for v in vlakna:
            v.start()
        for v in vlakna:
            v.join()
        self.assertLess(time.time() - start, 2.0)

    def test_dlouha_fronta_dotaz_vynecha_bez_ucasti_serveru(self):
        pt_api.MIN_GAP, pt_api.MAX_QUEUE_WAIT = 10.0, 1.0
        api = PrehrajtoApi("", "")
        api._open = lambda *a, **k: _Odpoved()
        api._page("/x")                     # obsadí termín
        with self.assertRaises(PrehrajtoError) as ctx:
            api._page("/y")
        self.assertTrue(ctx.exception.paused)
        self.assertNotIsInstance(ctx.exception, PrehrajtoRateLimited)


class _Odpoved:
    def read(self):
        return b"<html></html>"

    def close(self):
        pass


class _JsonOdpoved:
    def __init__(self, text):
        self._text = text.encode("utf-8")

    def read(self):
        return self._text

    def close(self):
        pass


# --- obnova tokenu ----------------------------------------------------------

class TestObnovaTokenu(unittest.TestCase):
    def _api_bez_bearer(self):
        """Skutečný `_api` (ne z FakeApi) s odchytem, kolikrát byl volán `_bearer`."""
        api = PrehrajtoApi("a@b.cz", "x")
        api._cookies = {"access_token": "vyprsely"}
        volani = {"bearer": 0}
        return api, volani

    def test_401_obnovi_token_a_zopakuje(self):
        api, volani = self._api_bez_bearer()
        api._bearer = lambda force=False: (volani.__setitem__("bearer", volani["bearer"] + 1),
                                           "old" if not force else "new")[1]

        stav = {"n": 0}

        def open_(url, headers=None, **kw):
            stav["n"] += 1
            if headers.get("Authorization") == "Bearer old":
                raise PrehrajtoError("HTTP 401", status=401)
            return _JsonOdpoved('{"status":"ok","payload":{"data":[]}}')

        api._open = open_
        payload = api._api("videos/search", {"phrase": "x"})
        self.assertEqual(payload, {"data": []})
        self.assertEqual(volani["bearer"], 2)      # nejdřív starý, pak vynucený nový
        self.assertEqual(stav["n"], 2)

    def test_jwt_expirace(self):
        import base64 as b64
        import json as j

        def token(exp):
            body = b64.urlsafe_b64encode(j.dumps({"exp": exp}).encode()).decode().rstrip("=")
            return "h." + body + ".s"

        self.assertTrue(pt_api._jwt_expired(token(time.time() - 10)))
        self.assertFalse(pt_api._jwt_expired(token(time.time() + 3600)))
        self.assertTrue(pt_api._jwt_expired("rozsypany"))


# --- stav účtu --------------------------------------------------------------

PROFIL_PREMIUM = '''<div class="user">
<span class="premium">PREMIUM </span><span>42 dní</span>
<a href="/odhlasit">Odhlásit se</a></div>'''
PROFIL_BEZ_PREMIUM = '<div class="user"><a href="/odhlasit">Odhlásit se</a></div>'


class TestStavUctu(unittest.TestCase):
    def setUp(self):
        pt_api._blocked_until = 0.0

    tearDown = setUp

    def test_bez_uctu_je_zdroj_v_poradku(self):
        zaznam = accounts_lib.prehrajto(FakeApi())
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
        api = FakeApi(email="a@b.cz", password="x")
        pt_api._note_block(None)
        zaznam = accounts_lib.prehrajto(api)
        self.assertEqual((zaznam["level"], zaznam["code"]), (accounts_lib.WARN, "paused"))
        self.assertEqual(api.dotazy, [])


class TestPrihlaseni(unittest.TestCase):
    def test_cookies_se_pamatuji(self):
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
            FakeApi(email="a@b.cz", password="x", cache=store).session()
            jiny = FakeApi(email="c@d.cz", password="y", cache=store)
            jiny.session()
            self.assertEqual(jiny.loginy, 1)

    def test_forget_zahodi_i_ulozenou_relaci(self):
        with tempfile.TemporaryDirectory() as tmp:
            from nokturno_core.lib.store import Store
            store = Store(tmp)
            api = FakeApi(email="a@b.cz", password="x", cache=store)
            api.session()
            api.forget()
            api.session()
            self.assertEqual(api.loginy, 2)


# --- zapojení do jádra ------------------------------------------------------

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

    def test_streamy_prochazi_filtrem_nazvu_bez_uctu(self):
        """Fulltext (HTML) vrací i cizí tituly — projít smí jen ten hledaný."""
        engine = self.engine()
        engine._pt = FakeApi({"/hledej/Matrix%201999": VYPIS + polozka(
            "77", "vikingove-1999", "aa11bb22cc33", "Vikingové 1999 CZ 1080p")})
        streams = engine._prehrajto_streams({"name": "Matrix", "year": "1999"}, None, "movie")
        self.assertTrue(streams)
        self.assertTrue(all("Matrix" in s["label"] for s in streams))
        self.assertTrue(all(s["url"].startswith("pt:") and s["source"] == "pt" for s in streams))

    def test_s_uctem_streamy_z_json_a_nesou_id(self):
        engine = self.engine(pt_email="a@b.cz", pt_password="x")
        engine._pt = FakeApi(vysledky={"Matrix 1999": VYSLEDKY}, email="a@b.cz", password="x")
        streams = engine._prehrajto_streams({"name": "Matrix", "year": "1999"}, None, "movie")
        self.assertTrue(streams)
        self.assertTrue(all(s["url"].startswith("pt:") and s["source"] == "pt" for s in streams))
        # url nese id → :<id>:<slug>:<hash>, tedy tři dvojtečky za „pt"
        self.assertTrue(all(s["url"].count(":") == 3 for s in streams))

    def test_velikost_se_ukaze_jen_s_uctem(self):
        """Velikost patří původnímu souboru — ten dostane jen Premium. Bez účtu by lhal."""
        engine = self.engine()
        engine._pt = FakeApi({"/hledej/Matrix%201999": VYPIS})
        self.assertTrue(all(not s["detail"] for s in
                            engine._prehrajto_streams({"name": "Matrix", "year": "1999"}, None, "movie")))
        engine = self.engine(pt_email="a@b.cz", pt_password="x")
        engine._pt = FakeApi(vysledky={"Matrix 1999": VYSLEDKY}, email="a@b.cz", password="x")
        self.assertTrue(all(s["detail"] for s in
                            engine._prehrajto_streams({"name": "Matrix", "year": "1999"}, None, "movie")))

    def test_429_prerusi_dalsi_dotazy(self):
        pt_api._blocked_until = 0.0
        self.addCleanup(setattr, pt_api, "_blocked_until", 0.0)
        engine = self.engine()

        class Omezeny(FakeApi):
            def _page(self, path, **kw):
                self.dotazy.append(path)
                raise PrehrajtoRateLimited("HTTP 429", status=429)

        engine._pt = Omezeny()
        failures = []
        self.assertEqual(engine._prehrajto_streams({"name": "Matrix", "year": "1999"},
                                                   None, "movie", failures=failures), [])
        self.assertEqual(len(engine._pt.dotazy), 1)
        self.assertEqual(failures[0][0], "Přehraj.to")

    def test_vypadek_jednoho_dotazu_hledani_neshodi(self):
        engine = self.engine()
        engine._pt = FakeApi()            # každá HTML cesta je 404
        failures = []
        self.assertEqual(engine._prehrajto_streams({"name": "Matrix", "year": "1999"},
                                                   None, "movie", failures=failures), [])
        self.assertTrue(failures)

    def test_resolve_odkazu_i_titulku_html(self):
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
        bez = self.engine()._streams_cache_key("movie", "tt0133093", None)
        s_uctem = self.engine(pt_email="a@b.cz", pt_password="x")._streams_cache_key(
            "movie", "tt0133093", None)
        vypnuty = Engine({}, tempfile.mkdtemp())._streams_cache_key("movie", "tt0133093", None)
        self.assertNotEqual(bez, s_uctem)
        self.assertNotEqual(bez, vypnuty)

    def test_hlavicka_souboru_se_cte(self):
        engine = self.engine()
        engine._pt = FakeApi({"/matrix-1999/66103820811ee": VIDEO})
        cteno = []
        engine._media_from_file = lambda url: cteno.append(url) or {}
        engine._fill_audio([{"url": "pt:matrix-1999:66103820811ee", "label": "Matrix"}])
        self.assertEqual(cteno, ["pt:matrix-1999:66103820811ee"])


if __name__ == "__main__":
    unittest.main()
