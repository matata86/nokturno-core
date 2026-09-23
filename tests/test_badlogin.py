"""Pauza po odmítnutém přihlášení (`lib/badlogin`) — bez sítě.

    python3 -m unittest tests.test_badlogin -v
"""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import badlogin                                    # noqa: E402
from nokturno_core.lib.store import Store                                 # noqa: E402
from nokturno_core.lib.sledujteto_api import SledujtetoApi, SledujtetoError  # noqa: E402
from nokturno_core.lib.fastshare_api import FastshareApi, FastshareError  # noqa: E402


class TestPauzaPoSpatnemHesle(unittest.TestCase):
    def setUp(self):
        badlogin._memory.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sledujteto_se_se_spatnym_heslem_nehlasi_znovu(self):
        volani = []

        def request(self, method, path, data=None, token=None):
            volani.append(path)
            raise SledujtetoError("přihlášení se nepovedlo", code="invalid_credentials", status=401)

        SledujtetoApi._request, puvodni = request, SledujtetoApi._request
        try:
            api = SledujtetoApi("a@b.cz", "spatne", cache=self.store)
            with self.assertRaises(SledujtetoError):
                api.login()
            badlogin._memory.clear()          # jiný proces: pauza musí platit i z úložiště
            with self.assertRaises(SledujtetoError) as ctx:
                SledujtetoApi("a@b.cz", "spatne", cache=self.store).login()
            self.assertTrue(ctx.exception.paused)
            self.assertEqual(ctx.exception.status, 401)
            self.assertEqual(len(volani), 1)
            with self.assertRaises(SledujtetoError):   # opravené heslo se zkusí hned
                SledujtetoApi("a@b.cz", "nove", cache=self.store).login()
            self.assertEqual(len(volani), 2)
        finally:
            SledujtetoApi._request = puvodni

    def test_vypadek_site_pauzu_nezaklada(self):
        def request(self, method, path, data=None, token=None):
            raise SledujtetoError("EOF occurred in violation of protocol")

        SledujtetoApi._request, puvodni = request, SledujtetoApi._request
        try:
            with self.assertRaises(SledujtetoError):
                SledujtetoApi("a@b.cz", "heslo", cache=self.store).login()
            self.assertFalse(badlogin.paused("sledujteto", "a@b.cz", "heslo", self.store))
        finally:
            SledujtetoApi._request = puvodni

    def test_fastshare_se_se_spatnym_heslem_nehlasi_znovu(self):
        volani = []

        def get(self, **params):
            volani.append(params)
            raise FastshareError("přihlášení se nepovedlo", status=401)

        FastshareApi._get, puvodni = get, FastshareApi._get
        try:
            for _ in range(3):
                with self.assertRaises(FastshareError):
                    FastshareApi("jmeno", "spatne", cache=self.store).login()
            self.assertEqual(len(volani), 1)
        finally:
            FastshareApi._get = puvodni


if __name__ == "__main__":
    unittest.main()
