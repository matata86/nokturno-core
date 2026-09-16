"""„Nastavit z mobilu“: QR kód bez PIL a krátkodobý server s jednorázovým klíčem."""
import ctypes
import ctypes.util
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nokturno_core.lib import qr, remote_setup  # noqa: E402

try:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    from qrcode.util import MODE_8BIT_BYTE, QRData
except ImportError:  # referenční knihovna je jen na vývojovém stroji
    qrcode = None

URL = "http://192.168.1.22:52100/s/Ab3xY9kLmQ_x-12345"


class TestQr(unittest.TestCase):
    @unittest.skipUnless(qrcode, "python-qrcode není nainstalovaný")
    def test_shoda_s_python_qrcode(self):
        for text in (URL, "x", "http://192.168.100.200:52109/s/" + "A" * 60, "c" * 213):
            for mask in range(8):
                ref = qrcode.QRCode(error_correction=ERROR_CORRECT_M, mask_pattern=mask, border=0)
                ref.add_data(QRData(text.encode(), mode=MODE_8BIT_BYTE))
                ref.make(fit=True)
                mine = qr.encode(text, mask=mask)
                self.assertEqual([[bool(v) for v in row] for row in ref.get_matrix()], mine, (text[:20], mask))

    def test_verze_a_limit(self):
        self.assertEqual(len(qr.encode(URL)), 33, "adresa s klíčem se vejde do verze 4")
        self.assertEqual(len(qr.encode("c" * 213)), 57)
        with self.assertRaises(ValueError):
            qr.encode("c" * 214)

    def test_png(self):
        matrix = qr.encode(URL)
        data = qr.to_png(matrix, scale=3, border=4)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        size = (len(matrix) + 8) * 3
        self.assertEqual(int.from_bytes(data[16:20], "big"), size)
        idat = data.index(b"IDAT")
        length = int.from_bytes(data[idat - 4:idat], "big")
        raw = zlib.decompress(data[idat + 4:idat + 4 + length])
        self.assertEqual(len(raw), size * (size + 1))

    @unittest.skipUnless(ctypes.util.find_library("zbar"), "libzbar není nainstalovaný")
    def test_precte_ctecka(self):
        z = ctypes.CDLL(ctypes.util.find_library("zbar"))
        z.zbar_image_scanner_create.restype = ctypes.c_void_p
        z.zbar_image_create.restype = ctypes.c_void_p
        z.zbar_image_first_symbol.restype = ctypes.c_void_p
        z.zbar_image_first_symbol.argtypes = [ctypes.c_void_p]
        z.zbar_symbol_get_data.restype = ctypes.c_char_p
        z.zbar_symbol_get_data.argtypes = [ctypes.c_void_p]
        matrix = qr.encode(URL)
        scale, border = 4, 4
        size = (len(matrix) + 2 * border) * scale
        pixels = bytearray(b"\xff" * size * size)
        for r, row in enumerate(matrix):
            for c, dark in enumerate(row):
                if dark:
                    for y in range((r + border) * scale, (r + border + 1) * scale):
                        start = y * size + (c + border) * scale
                        pixels[start:start + scale] = b"\x00" * scale
        scanner = ctypes.c_void_p(z.zbar_image_scanner_create())
        z.zbar_image_scanner_set_config(scanner, 0, 0, 1)
        image = ctypes.c_void_p(z.zbar_image_create())
        z.zbar_image_set_format(image, ctypes.c_ulong(0x30303859))
        z.zbar_image_set_size(image, size, size)
        buf = ctypes.create_string_buffer(bytes(pixels), len(pixels))
        z.zbar_image_set_data(image, buf, len(pixels), None)
        z.zbar_scan_image(scanner, image)
        symbol = z.zbar_image_first_symbol(image.value)
        self.assertTrue(symbol)
        self.assertEqual(z.zbar_symbol_get_data(symbol).decode(), URL)


SCHEMA = [
    {"id": "ws", "label": "WebShare", "open": True, "fields": [
        {"id": "ws_enabled", "label": "Zapnout", "type": "bool"},
        {"id": "ws_username", "label": "Uživatel", "type": "text", "enable": ("ws_enabled", "true")},
        {"id": "ws_password", "label": "Heslo", "type": "password", "enable": ("ws_enabled", "true")},
    ]},
    {"id": "playback", "label": "Přehrávání", "fields": [
        {"id": "pref_lang", "label": "Jazyk", "type": "choice", "options": [("0", "Jakýkoli"), ("1", "CZ")]},
    ]},
]
VALUES = {"ws_enabled": "false", "ws_username": "stary", "ws_password": "tajne-heslo-123", "pref_lang": "1"}


class TestServer(unittest.TestCase):
    def setUp(self):
        self.server = remote_setup.SetupServer(SCHEMA, VALUES, token="klic-123")
        self.server.start(host="127.0.0.1", ports=[0])
        self.base = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode()

    def post(self, form, path="/s/klic-123"):
        body = urllib.parse.urlencode(form).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=body), timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode()

    def test_stranka_jen_s_klicem_a_bez_hesla(self):
        self.assertEqual(self.get("/")[0], 404)
        self.assertEqual(self.get("/s/spatny")[0], 404)
        status, page = self.get("/s/klic-123")
        self.assertEqual(status, 200)
        self.assertIn('value="stary"', page)
        self.assertNotIn("tajne-heslo-123", page, "heslo se na stránku nikdy neposílá")
        self.assertIn("nech prázdné", page)
        self.assertIn('<option value="1" selected>', page)

    def test_odeslani_vrati_jen_zmeny(self):
        status, _ = self.post({"ws_enabled": "on", "ws_username": " novy ", "ws_password": "", "pref_lang": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(self.server.wait_result(1), {"ws_enabled": "true", "ws_username": "novy"})
        self.assertEqual(self.post({"pref_lang": "0"})[0], 410, "přijme se jen jedno odeslání")
        self.assertEqual(self.get("/s/klic-123")[0], 410)

    def test_neplatna_volba(self):
        status, page = self.post({"pref_lang": "9"})
        self.assertEqual(status, 400)
        self.assertIn("Neplatná hodnota: Jazyk", page)
        self.assertFalse(self.server.finished)

    def test_nove_heslo_a_vypnuti(self):
        self.post({"ws_username": "stary", "ws_password": "nove", "pref_lang": "1"})
        self.assertEqual(self.server.wait_result(1), {"ws_password": "nove"})

    def test_mnoho_spatnych_pokusu_server_ukonci(self):
        for _ in range(remote_setup.MAX_BAD_REQUESTS):
            self.get("/s/hadam")
        self.assertIsNone(self.server.wait_result(2))
        self.assertTrue(self.server.finished)

    def test_wait_until(self):
        stop = threading.Event()
        self.assertEqual(remote_setup.wait_until(self.server, stop.is_set, 0.3, tick=0.05), ("timeout", None))
        stop.set()
        self.assertEqual(remote_setup.wait_until(self.server, stop.is_set, 5, tick=0.05), ("stopped", None))
        self.post({"pref_lang": "0", "ws_username": "stary"})
        self.assertEqual(remote_setup.wait_until(self.server, lambda: False, 5), ("saved", {"pref_lang": "0"}))


if __name__ == "__main__":
    unittest.main()
