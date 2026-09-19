"""Přesměrování nesmí odnést Authorization/Cookie na cizí host (audit 2026-09-19).

Dva lokální servery: `127.0.0.1` a `localhost` jsou pro `urllib` různé hosty, i když
je to tentýž stroj — přesně to stačí k ověření, že se hlavičky na cizí host neposílají
a na tentýž host (jiná cesta) posílají dál.
"""
import http.server
import pathlib
import sys
import threading
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import mediainfo, safe_redirect   # noqa: E402


class _Server:
    def __init__(self):
        prijate = self.prijate = []
        cil = self.cil = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                prijate.append((self.path, self.headers.get("Authorization"), self.headers.get("Cookie")))
                if self.path.startswith("/presmeruj"):
                    self.send_response(302)
                    self.send_header("Location", cil["url"])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                telo = b"0123456789" * 100
                self.send_response(206 if self.headers.get("Range") else 200)
                self.send_header("Content-Length", str(len(telo)))
                self.end_headers()
                self.wfile.write(telo)

            def log_message(self, *a):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestPresmerovaniBezCredentials(unittest.TestCase):
    def setUp(self):
        self.a = _Server()
        self.b = _Server()
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)

    def test_cizi_host_hlavicky_nedostane(self):
        self.a.cil["url"] = f"http://localhost:{self.b.port}/soubor.mkv"
        req = urllib.request.Request(f"http://127.0.0.1:{self.a.port}/presmeruj",
                                     headers={"Authorization": "Basic dGFqbmU=", "Cookie": "FASTSHARE=x"})
        with safe_redirect.OPENER.open(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(self.a.prijate[0][1], "Basic dGFqbmU=")
        self.assertEqual(self.b.prijate, [("/soubor.mkv", None, None)])

    def test_stejny_host_hlavicky_dostane(self):
        self.a.cil["url"] = f"http://127.0.0.1:{self.a.port}/jinde.mkv"
        req = urllib.request.Request(f"http://127.0.0.1:{self.a.port}/presmeruj",
                                     headers={"Authorization": "Basic dGFqbmU="})
        with safe_redirect.OPENER.open(req, timeout=5):
            pass
        self.assertEqual([p[1] for p in self.a.prijate], ["Basic dGFqbmU=", "Basic dGFqbmU="])

    def test_mediainfo_bez_openeru_jde_pres_bezpecny(self):
        self.a.cil["url"] = f"http://localhost:{self.b.port}/soubor.mkv"
        url = f"http://127.0.0.1:{self.a.port}/presmeruj|Authorization=Basic%20dGFqbmU%3D"
        data, _total = mediainfo.fetch_sized(url, length=64)
        self.assertEqual(len(data), 64)
        self.assertEqual(self.b.prijate[0][1], None, "Basic auth nesmí odejít na jiný host")


if __name__ == "__main__":
    unittest.main()
