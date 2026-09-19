"""CZtor — klient a zapojení do jádra, nad odpověďmi zachycenými z živého API
(2026-09-19, zkrácené). Bez sítě: `CztorApi._http` nahrazuje slovník cest.

    python3 -m unittest tests.test_cztor_api -v
"""
import pathlib
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core import Engine                                          # noqa: E402
from nokturno_core.engine import NokturnoError                           # noqa: E402
from nokturno_core.lib.cztor_api import (CztorApi, CztorError, NotPaired,  # noqa: E402
                                         media_info, parse_ref)
from nokturno_core.lib.store import Store                                # noqa: E402
from nokturno_core.lib.streams import parse_stream                       # noqa: E402


def title(id_, type_, name, orig, year, imdb=None, csfd=None):
    return {"id": id_, "type": type_, "title": name, "name": name, "original_title": orig, "year": year,
            "ids": {"imdb": imdb, "tmdb": None, "csfd": csfd}}


MATRIX = title(296, "movie", "Matrix", "Matrix", 1999, "tt0133093")
INCEPTION = title(9820, "movie", "Počiatok", "Inception", 2010, "tt1375666")
RELOADED = title(167, "movie", "Matrix Reloaded", "Matrix Reloaded", 2003, "tt0234215")
PREBOR = title(10442, "show", "Okresný prebor", "Okresný prebor", 2010, None, "254790")
ZRADCI = title(7336, "show", "Zrádci - Série 1", "Zrádci - Série 1", 2024, "tt33321873")

STREAM_4K = {
    "id": 13060, "playback_url": "https://zeus.giganthost.com/abc", "storage": 1,
    "release_name": "The.Matrix.1999.2160p.UHD.BluRay.TrueHD.7.1.HDR.x265-DON.CZ-FTU.mkv",
    "quality": "4k", "width": 3840, "height": 1600, "audio_languages": ["CZ", "EN"],
    "audio_tracks": [{"index": 1, "language": "CZ", "codec": "AC3", "channels": "2.0"},
                     {"index": 2, "language": "EN", "codec": "TrueHD", "channels": "7.1"}],
    "subtitles": ["CZ"], "subtitle_tracks": [{"index": 8, "language": "CZ", "codec": "SRT"}],
    "size_bytes": 42988658718, "duration_seconds": 8179, "hdr": True, "dolby_vision": False,
}
STREAM_SD = {
    "id": 34655, "playback_url": "https://zeus.giganthost.com/def", "release_name": "Matrix 1..avi",
    "quality": "480p", "width": 720, "height": 304, "audio_languages": [], "audio_tracks": [],
    "subtitles": [], "size_bytes": 1560631296, "hdr": False,
}
EPISODE_STREAM = {"id": 38011, "playback_url": "https://zeus.giganthost.com/ep2",
                  "release_name": "Okresní přebor-S01E02-Nábor.mkv", "width": 1280, "height": 720,
                  "audio_languages": ["CZ"], "audio_tracks": [{"language": "CZ", "channels": "2.0"}],
                  "size_bytes": 938488242}


class FakeServer:
    """Odpovědi podle (metoda, cesta); počítá volání a umí obnovu tokenu."""

    def __init__(self):
        self.calls = []
        self.search = {
            "Matrix": [INCEPTION, RELOADED, MATRIX],
            "Okresní přebor": [PREBOR, title(9193, "movie", "Okresní přebor - Poslední zápas Pepika Hnátka",
                                                "Okresní přebor - Poslední zápas Pepika Hnátka", 2012)],
            "Zrádci": [ZRADCI],
        }
        self.valid_access = {"A1"}
        self.valid_refresh = {"R1"}
        self.issued = 1
        self.streams = {"/titles/296/streams": [STREAM_4K, STREAM_SD],
                        "/episodes/23374/streams": [EPISODE_STREAM],
                        "/episodes/7401/streams": [dict(EPISODE_STREAM, id=5, release_name="Zradci.S01E03.mkv")]}

    def __call__(self, method, path, params=None, payload=None, token=None):
        self.calls.append((method, path))
        if path == "/auth/refresh":
            if payload["refresh_token"] not in self.valid_refresh:
                raise CztorError("Refresh token is invalid or expired.", status=401)
            self.valid_refresh.discard(payload["refresh_token"])   # obnovovací token se použitím mění
            self.issued += 1
            access, refresh = f"A{self.issued}", f"R{self.issued}"
            self.valid_access.add(access)
            self.valid_refresh.add(refresh)
            return {"access_token": access, "refresh_token": refresh, "expires_at": "2099-01-01T00:00:00+02:00"}
        if path == "/auth/pin/start":
            return {"pin_code": "434252", "poll_token": "P", "expires_at": "2099-01-01T00:00:00+02:00", "interval": 5}
        if path == "/auth/pin/poll":
            return {"status": "authorized", "access_token": "A1", "refresh_token": "R1",
                    "expires_at": "2099-01-01T00:00:00+02:00", "user": {"name": "Tester"}}
        if token not in self.valid_access:
            raise CztorError("Unauthenticated.", status=401)
        if path == "/profile":
            return {"user": {"name": "Tester"}, "subscription": {"active": True, "plan": "Basic",
                                                                 "valid_until": "2026-10-13T11:40:02+02:00"}}
        if path == "/search":
            return {"items": self.search.get(params["q"], [])}
        if path == "/shows/10442/seasons":
            return {"items": [{"id": 2680, "season_number": 1}]}
        if path == "/shows/10442/seasons/2680/episodes":
            return {"items": [{"id": 23371, "episode_number": 1}, {"id": 23374, "episode_number": 2}]}
        if path == "/shows/7336/seasons":
            return {"items": [{"id": 900, "season_number": 1}]}
        if path == "/shows/7336/seasons/900/episodes":
            return {"items": [{"id": 7401, "episode_number": 3}]}
        if path in self.streams:
            return {"streams": self.streams[path]}
        raise CztorError("not found", status=404)


def paired_api(tmp, server=None, expires=None):
    store = Store(tmp)
    store.save("cztor_session", {"device_id": "dev", "access_token": "A1", "refresh_token": "R1",
                                 "expires": time.time() + 3600 if expires is None else expires})
    api = CztorApi(store)
    api._http = server or FakeServer()
    return api


class TestKlient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_parovani_pinem_ulozi_tokeny_a_ucet(self):
        api = CztorApi(Store(self.tmp))
        api._http = FakeServer()
        self.assertFalse(api.paired())
        pin = api.start_pin()
        self.assertEqual((pin["pin"], pin["url"]), ("434252", "https://cztor.com/activate"))
        self.assertTrue(api.poll_pin(pin["poll_token"]))
        self.assertTrue(api.paired())
        self.assertEqual(api.account()["plan"], "Basic")
        # id zařízení zůstane i po odhlášení — nové párování je totéž zařízení
        device = api.device_id()
        api.logout()
        self.assertFalse(api.paired())
        self.assertEqual(api.device_id(), device)

    def test_bez_parovani(self):
        api = CztorApi(Store(self.tmp))
        api._http = FakeServer()
        with self.assertRaises(NotPaired):
            api.search("Matrix")

    def test_vyprseny_token_se_obnovi_a_ulozi_novy_par(self):
        server = FakeServer()
        api = paired_api(self.tmp, server, expires=0)
        self.assertEqual(api.profile()["plan"], "Basic")
        self.assertIn(("POST", "/auth/refresh"), server.calls)
        session = api.store.load("cztor_session", {})
        self.assertEqual((session["access_token"], session["refresh_token"]), ("A2", "R2"))

    def test_401_za_behu_obnovi_token(self):
        server = FakeServer()
        server.valid_access = set()          # token zneplatněný na serveru dřív, než vypršel
        api = paired_api(self.tmp, server)
        self.assertEqual(api.profile()["plan"], "Basic")

    def test_obnovu_mezitim_udelal_nekdo_jiny(self):
        """Plugin a služba Kodi sdílí úložiště: kdo přijde s už použitým obnovovacím
        tokenem, nemá párování zahodit, ale vzít ten nový."""
        server = FakeServer()
        api = paired_api(self.tmp, server, expires=0)
        druhy = CztorApi(api.store)
        druhy._http = server
        druhy.profile()                     # obnoví R1 → R2
        api.store.save("cztor_session", dict(api.store.load("cztor_session", {}), expires=0))
        self.assertEqual(api.profile()["plan"], "Basic")
        self.assertTrue(api.paired())

    def test_zamitnuta_obnova_zrusi_parovani(self):
        server = FakeServer()
        server.valid_refresh = set()
        api = paired_api(self.tmp, server, expires=0)
        with self.assertRaises(NotPaired):
            api.profile()
        self.assertFalse(api.paired())

    def test_film_podle_imdb_ne_podle_fulltextu(self):
        api = paired_api(self.tmp)
        found = api.find_titles("movie", ["Matrix"], 1999, imdb="tt0133093")
        self.assertEqual([i["id"] for i in found], [296])

    def test_serial_bez_id_podle_slovenskeho_nazvu(self):
        api = paired_api(self.tmp)
        found = api.find_titles("series", ["Okresní přebor"], 2010)
        self.assertEqual([i["id"] for i in found], [10442])
        self.assertEqual(api.episode_id(found[0], 1, 2), "23374")
        self.assertIsNone(api.episode_id(found[0], 2, 1))

    def test_podobny_nazev_jen_s_presnym_rokem(self):
        api = paired_api(self.tmp)
        self.assertEqual(api.find_titles("series", ["Okresní přebor"], 2011), [])

    def test_serial_rozdeleny_po_seriich(self):
        api = paired_api(self.tmp)
        found = api.find_titles("series", ["Zrádci"], 2024, imdb="tt33321873")
        self.assertEqual(found[0]["_split_season"], 1)
        self.assertEqual(api.episode_id(found[0], 1, 3), "7401")
        self.assertIsNone(api.episode_id(found[0], 2, 3))

    def test_stream_nese_udaje_o_souboru_a_odkaz_bez_adresy(self):
        api = paired_api(self.tmp)
        stream = api.streams("m", 296)[0]
        self.assertEqual(stream["ref"], "cz:m:296:13060")
        self.assertNotIn("giganthost", stream["ref"])
        self.assertEqual(stream["media"]["audio"][1], {"lang": "EN", "channels": "7.1", "codec": "TrueHD"})
        self.assertEqual(stream["media"]["subs"], ["CZ"])
        self.assertEqual(parse_ref(stream["ref"]), ("m", "296", "13060"))

    def test_adresa_se_bere_cerstva(self):
        server = FakeServer()
        api = paired_api(self.tmp, server)
        self.assertEqual(api.resolve("cz:m:296:34655"), "https://zeus.giganthost.com/def")
        with self.assertRaises(CztorError):
            api.resolve("cz:m:296:1")
        with self.assertRaises(CztorError):
            api.resolve("cz:m:296")

    def test_jazyky_bez_stop(self):
        info = media_info({"audio_languages": ["cs", "SK"], "subtitles": ["en"], "size_bytes": "5"})
        self.assertEqual([t["lang"] for t in info["audio"]], ["CZ", "SK"])
        self.assertEqual((info["subs"], info["size"]), (["EN"], 5))


class TestVEnginu(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def engine(self, enabled=True):
        eng = Engine({"cz_enabled": enabled}, self.tmp)
        paired_api(self.tmp)                       # uloží spárovanou relaci do úložiště enginu
        eng.cztor_client = lambda: self._client(eng)
        eng.original_titles = lambda meta, ctype, alt=None: []
        return eng

    def _client(self, eng):
        api = CztorApi(eng.store)
        api._http = self.server
        return api

    def test_vypnuty_prepinac(self):
        self.server = FakeServer()
        eng = self.engine(enabled=False)
        self.assertEqual(eng._cztor_streams({"name": "Matrix", "year": 1999, "id": "tt0133093"}), [])
        self.assertFalse(eng.sources()["cztor"])

    def test_streamy_filmu_jsou_overene(self):
        self.server = FakeServer()
        eng = self.engine()
        self.assertTrue(eng.sources()["cztor"])
        found = eng._cztor_streams({"name": "Matrix", "year": 1999, "id": "tt0133093"})
        self.assertEqual([s["url"] for s in found], ["cz:m:296:13060", "cz:m:296:34655"])
        top = parse_stream(found[0])
        self.assertEqual((top["quality_rank"], sorted(top["langs"]), sorted(top["subs"])), (4, ["CZ", "EN"], ["CZ"]))
        self.assertEqual(top["channels"], {"CZ": 2.0, "EN": 7.1})
        self.assertFalse(top.get("_langs_from_name"))
        self.assertTrue(top["_tracks"])      # hlavička se číst nebude
        self.assertGreater(top["size_gb"], 39)
        self.assertTrue(found[0]["label"].endswith(".mkv"))   # HDR už v názvu je, značka se nepřidá

    def test_dil_serialu(self):
        self.server = FakeServer()
        eng = self.engine()
        found = eng._cztor_streams({"name": "Okresní přebor", "year": 2010}, {"season": 1, "episode": 2}, "series")
        self.assertEqual([s["url"] for s in found], ["cz:e:23374:38011"])

    def test_vypadek_je_hlaseny(self):
        self.server = FakeServer()
        self.server.valid_refresh = set()
        eng = self.engine()
        eng.store.save("cztor_session", dict(eng.store.load("cztor_session", {}), expires=0))
        failures = []
        self.assertEqual(eng._cztor_streams({"name": "Matrix", "id": "tt0133093"}, failures=failures), [])
        self.assertEqual(failures[0][0], "CZtor")

    def test_prehrani(self):
        self.server = FakeServer()
        eng = self.engine()
        self.assertEqual(eng.resolve("cz:m:296:13060"), "https://zeus.giganthost.com/abc")
        with self.assertRaises(NokturnoError):
            eng.resolve("cz:m:296:1")

    def test_luna_si_stream_cztor_neprivlastni(self):
        """Stejně velký soubor z Luny a z CZtor zůstanou dva řádky — `_merge_direct`
        přibaluje k Luně jen WebShare/HellSpy, CZtor má vlastní odkaz i údaje."""
        luna = parse_stream({"url": "http://luna/1", "label": "Luna CZ", "detail": "40.0 GB", "quality": "4K",
                             "source": "main"})
        self.server = FakeServer()
        cz = parse_stream(self.engine()._cztor_streams({"name": "Matrix", "id": "tt0133093"})[0])
        merged = Engine._merge_direct([luna, cz])
        self.assertEqual(len(merged), 2)
        self.assertNotIn("_ws_url", merged[0])


if __name__ == "__main__":
    unittest.main()
