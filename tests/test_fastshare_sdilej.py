"""Účet ze Sdilej.cz nad katalogem FastShare (lib/fastshare_api)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nokturno_core.lib import fastshare_api  # noqa: E402
from nokturno_core.lib.fastshare_api import FastshareApi, normalize, parse_ref  # noqa: E402


class TestSdilej(unittest.TestCase):
    def test_server_bez_cisla(self):
        # hledání vrací i `data.fastshare.cloud` — dřív se takový soubor zahodil
        f = normalize({"download_url": "https://data.fastshare.cloud/download.php?id=26091731", "filename": "x.mkv"})
        self.assertEqual((f["id"], f["server"]), ("26091731", "data"))
        self.assertEqual(parse_ref("fs:26091731:data:5")[0], "https://data.fastshare.cloud/download.php?id=26091731")

    def test_odkaz_podle_poskytovatele(self):
        self.assertEqual(parse_ref("fs:1:data8:0", "sdilej")[0], "https://data8.sdilej.cz/sdilej_profi.php?id=1")

    def test_prihlaseni_a_cookie_sdilej(self):
        volani = []

        def get(self, api=fastshare_api.API, **params):
            volani.append(api)
            return {"user": {"hash": "abc", "unlimited": "True", "data": {"value": 0}}}

        FastshareApi._get, puvodni = get, FastshareApi._get
        try:
            api = FastshareApi("jmeno", "heslo", provider="sdilej")
            url, headers = api.request("fs:26227575:data8:1249651197")
        finally:
            FastshareApi._get = puvodni
        self.assertEqual(volani, ["https://sdilej.cz/api/api_kodi.php"])
        self.assertEqual(url, "https://data8.sdilej.cz/sdilej_profi.php?id=26227575")
        self.assertEqual(headers["Cookie"], "SDILEJ=abc")

    def test_vychozi_zustava_fastshare(self):
        api = FastshareApi("jmeno", "heslo", provider="nesmysl")
        self.assertEqual(api.provider, "fastshare")
        # otisk účtu FastShare se nemění, uložené hashe platí dál
        self.assertNotEqual(api._account_key(), FastshareApi("jmeno", "heslo", provider="sdilej")._account_key())


if __name__ == "__main__":
    unittest.main()
