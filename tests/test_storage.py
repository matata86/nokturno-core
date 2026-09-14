"""Vlastní úložiště (WebDAV) — proti malému serveru na localhostu, bez vnější sítě.

    python3 -m unittest tests.test_storage -v
"""
import base64
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core import Engine                                         # noqa: E402
from nokturno_core.lib import mediainfo                                  # noqa: E402
from nokturno_core.lib.storage_api import (StorageApi, StorageError, match_texts,  # noqa: E402
                                           normalize_url, parse_ref, safe_path)

# strom úložiště: složka → [(jméno, je_složka, velikost)]
STROM = {
    "/dav/": [("Filmy", True, 0), ("Serialy", True, 0), ("@eaDir", True, 0), ("readme.txt", False, 10)],
    "/dav/Filmy/": [("Matrix (1999)", True, 0), ("Pelisky.1999.1080p.CZ.mkv", False, 4 * 2 ** 30),
                    ("Matrix.Reloaded.2003.mkv", False, 2 ** 30)],
    "/dav/Filmy/Matrix (1999)/": [("matrix.2160p.remux.mkv", False, 60 * 2 ** 30)],
    "/dav/Serialy/": [("Sherlock", True, 0)],
    "/dav/Serialy/Sherlock/": [("Season 1", True, 0)],
    "/dav/Serialy/Sherlock/Season 1/": [("S01E02.mkv", False, 2 ** 30), ("S01E03.mkv", False, 2 ** 30)],
    "/dav/@eaDir/": [("skryte.mkv", False, 1)],
}
AUTH = "Basic " + base64.b64encode(b"nokturno:tajne").decode()


class Dav(BaseHTTPRequestHandler):
    html_only = False

    def log_message(self, *a):
        pass

    def _auth(self):
        if self.headers.get("Authorization") != AUTH:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True

    def do_PROPFIND(self):
        if not self._auth():
            return
        if self.server.html_only:
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = urllib.parse.unquote(self.path)
        if path not in STROM:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        rows = [f"<d:response><d:href>{urllib.parse.quote(path)}</d:href><d:propstat><d:prop>"
                "<d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>"]
        for name, is_dir, size in STROM[path]:
            href = urllib.parse.quote(path + name + ("/" if is_dir else ""))
            kind = "<d:collection/>" if is_dir else ""
            rows.append(f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype>{kind}"
                        f"</d:resourcetype><d:getcontentlength>{size}</d:getcontentlength></d:prop>"
                        "</d:propstat></d:response>")
        body = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(rows)
                + "</d:multistatus>").encode()
        self.send_response(207)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._auth():
            return
        path = urllib.parse.unquote(self.path)
        if path == "/dav/.nokturno-rev":
            if self.server.rev is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = self.server.rev.encode()
        elif path.endswith("/"):
            links = "".join(f'<a href="{urllib.parse.quote(n)}{"/" if d else ""}">{n}</a>'
                            for n, d, _s in STROM.get(path, []))
            body = f'<a href="?C=N;O=D">Name</a><a href="../">Parent</a>{links}'.encode()
        else:
            self.server.last_range = self.headers.get("Range")
            body = b"\x00" * 16
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server:
    def __enter__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Dav)
        self.httpd.html_only = False
        self.httpd.last_range = None
        self.httpd.rev = None
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/dav/"
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestAdresyACesty(unittest.TestCase):
    def test_normalizace_adresy(self):
        self.assertEqual(normalize_url("davs://nas.lan:10000/video"), "https://nas.lan:10000/video/")
        self.assertEqual(normalize_url("dav://1.2.3.4:8090"), "http://1.2.3.4:8090/")
        self.assertEqual(normalize_url("nas.lan/dav/"), "http://nas.lan/dav/")
        self.assertEqual(normalize_url("https://jmeno:heslo@nas.lan/x/"), "https://nas.lan/x/")
        self.assertEqual(normalize_url("ftp://nas.lan/"), "")
        self.assertEqual(normalize_url(""), "")

    def test_cesta_nesmi_ven(self):
        for spatna in ("../etc/passwd", "Filmy/../../x", "a//b", "a\\b", ""):
            with self.assertRaises(StorageError, msg=spatna):
                safe_path(spatna)
        self.assertEqual(safe_path("/Filmy/Matrix (1999)/m.mkv"), "Filmy/Matrix (1999)/m.mkv")

    def test_odkaz_na_soubor(self):
        self.assertEqual(parse_ref("dav:2:Filmy/a b.mkv"), (2, "Filmy/a b.mkv"))
        for spatny in ("dav:4:x.mkv", "dav:0:x.mkv", "dav:1:../x", "ws:abc", "dav:x:y"):
            with self.assertRaises(StorageError, msg=spatny):
                parse_ref(spatny)

    def test_texty_pro_prirazeni(self):
        self.assertEqual(match_texts("Serialy/Sherlock/Season 1/S01E02.mkv"),
                         ["S01E02.mkv", "Season 1 S01E02.mkv", "Sherlock S01E02.mkv", "Serialy S01E02.mkv"])

    def test_kodi_adresa_nese_heslo_za_svislitkem(self):
        api = StorageApi("http://nas/dav/", "nokturno", "tajne")
        url = api.kodi_url("Filmy/Matrix (1999).mkv")
        base, extra = mediainfo.split_headers(url)
        self.assertEqual(base, "http://nas/dav/Filmy/Matrix%20%281999%29.mkv")
        self.assertEqual(extra["Authorization"], AUTH)
        self.assertEqual(StorageApi("http://nas/dav/").kodi_url("a.mkv"), "http://nas/dav/a.mkv")


class TestProchazeni(unittest.TestCase):
    def test_projde_strom_a_vezme_jen_videa(self):
        with Server() as srv:
            files = StorageApi(srv.url, "nokturno", "tajne").files()
        self.assertEqual([f["path"] for f in files], [
            "Filmy/Matrix (1999)/matrix.2160p.remux.mkv", "Filmy/Matrix.Reloaded.2003.mkv",
            "Filmy/Pelisky.1999.1080p.CZ.mkv", "Serialy/Sherlock/Season 1/S01E02.mkv",
            "Serialy/Sherlock/Season 1/S01E03.mkv"])
        self.assertEqual(files[2]["size_h"], "4.0 GB")

    def test_bez_webdav_projde_html_vypis(self):
        with Server() as srv:
            srv.httpd.html_only = True
            files = StorageApi(srv.url, "nokturno", "tajne").files()
        self.assertEqual(len(files), 5)

    def test_spatne_heslo(self):
        with Server() as srv:
            with self.assertRaises(StorageError) as ctx:
                StorageApi(srv.url, "nokturno", "spatne").check()
        self.assertEqual(ctx.exception.status, 401)

    def test_hledani_ve_slozkach_i_nazvech(self):
        with Server() as srv:
            api = StorageApi(srv.url, "nokturno", "tajne")
            found, total = api.search("sherlock s01e03")
            self.assertEqual((total, found[0]["name"]), (1, "S01E03.mkv"))
            self.assertEqual(api.search("matrix")[1], 2)

    def test_znacka_zmeny_vynuti_nove_prochazeni(self):
        from nokturno_core.lib.store import Store
        with Server() as srv:
            api = StorageApi(srv.url, "nokturno", "tajne", cache=Store(tempfile.mkdtemp()))
            self.assertEqual(len(api.files()), 5)
            STROM["/dav/Filmy/"].append(("Novy.Film.2026.mkv", False, 1))
            try:
                self.assertEqual(len(api.files()), 5, "bez značky platí hodinová paměť")
                srv.httpd.rev = "1789300000.5"
                api._rev = (0.0, "")   # značka se čte nejvýš jednou za REV_TTL — tady jako po minutě
                self.assertEqual(len(api.files()), 6, "nová značka = nové procházení")
                self.assertEqual(api.revision(), "1789300000.5")
            finally:
                STROM["/dav/Filmy/"].pop()

    def test_hlavicka_souboru_se_cte_s_heslem(self):
        with Server() as srv:
            api = StorageApi(srv.url, "nokturno", "tajne")
            data = mediainfo.fetch(api.kodi_url("Filmy/Pelisky.1999.1080p.CZ.mkv"), length=16)
            self.assertEqual(len(data), 16)
            self.assertEqual(srv.httpd.last_range, "bytes=0-15")


class TestVEnginu(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def engine(self, url):
        return Engine({"dav2_url": url, "dav2_username": "nokturno", "dav2_password": "tajne",
                       "dav2_name": "NAS"}, self.tmp)

    def test_film_najde_soubor_i_podle_slozky(self):
        with Server() as srv:
            eng = self.engine(srv.url)
            eng.original_titles = lambda meta, ctype, alt=None: []
            found = eng._storage_streams({"name": "Matrix", "year": 1999}, None, "movie")
        self.assertEqual([s["url"] for s in found], ["dav:2:Filmy/Matrix (1999)/matrix.2160p.remux.mkv"])
        self.assertEqual(found[0]["_storage"], "NAS")

    def test_dil_serialu(self):
        with Server() as srv:
            eng = self.engine(srv.url)
            eng.original_titles = lambda meta, ctype, alt=None: []
            found = eng._storage_streams({"name": "Sherlock"}, {"season": 1, "episode": 3}, "series")
        self.assertEqual([s["url"] for s in found], ["dav:2:Serialy/Sherlock/Season 1/S01E03.mkv"])

    def test_prehrani_bere_server_z_nastaveni(self):
        eng = self.engine("http://nas.lan/dav/")
        self.assertTrue(eng.sources()["storage"])
        self.assertTrue(eng.resolve("dav:2:Filmy/a.mkv").startswith("http://nas.lan/dav/Filmy/a.mkv|Authorization="))
        url, headers = eng.storage_request("dav:2:Filmy/a.mkv")
        self.assertEqual((url, headers["Authorization"]), ("http://nas.lan/dav/Filmy/a.mkv", AUTH))
        from nokturno_core.engine import NokturnoError
        for spatny in ("dav:1:Filmy/a.mkv", "dav:2:../a.mkv"):
            with self.assertRaises(NokturnoError, msg=spatny):
                eng.resolve(spatny)

    def test_vypadek_uloziste_je_hlaseny(self):
        eng = self.engine("http://127.0.0.1:9/dav/")
        eng.original_titles = lambda meta, ctype, alt=None: []
        failures = []
        self.assertEqual(eng._storage_streams({"name": "Matrix"}, None, "movie", failures=failures), [])
        self.assertEqual(failures[0][0], "NAS")


if __name__ == "__main__":
    unittest.main()
