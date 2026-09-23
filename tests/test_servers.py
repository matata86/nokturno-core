"""Dvě adresy serveru (`nokturno_core/lib/servers.py`) — bez sítě.

Hlídá, že výpadek jedné adresy klienta nepoloží: zkusí druhou. A že odpověď
serveru (HTTP chyba) druhou adresu nespustí — vedou na tentýž stroj.
"""
import pathlib
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import servers  # noqa: E402


class Resp:
    def __init__(self, url):
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDveAdresy(unittest.TestCase):
    def setUp(self):
        servers._dead.clear()
        self.addCleanup(servers._dead.clear)

    def url(self, base):
        return base + "/trending?kind=movie"

    def test_prvni_adresa_je_vlastni_domena(self):
        self.assertEqual(servers.BASE, "https://nokturno.stream")
        self.assertEqual(servers.BASES[1], "https://nokturno.tailf0014.ts.net")

    def test_pri_vypadku_prvni_se_zkusi_druha(self):
        volano = []

        def fake(req, timeout=None):
            url = req.full_url if isinstance(req, urllib.request.Request) else req
            volano.append(url)
            if url.startswith(servers.BASES[0]):
                raise urllib.error.URLError("nedostupné")
            return Resp(url)

        with mock.patch("urllib.request.urlopen", fake):
            with servers.urlopen(self.url(servers.BASES[0]), timeout=5) as resp:
                self.assertTrue(resp.url.startswith(servers.BASES[1]))
        self.assertEqual(len(volano), 2)

    def test_telo_a_metoda_se_prenesou_na_druhou_adresu(self):
        videno = {}

        def fake(req, timeout=None):
            if req.full_url.startswith(servers.BASES[0]):
                raise OSError("spojení odmítnuto")
            videno["metoda"] = req.get_method()
            videno["data"] = req.data
            videno["agent"] = req.get_header("User-agent")
            return Resp(req.full_url)

        req = urllib.request.Request(servers.BASES[0] + "/collect", data=b'{"a":1}',
                                     headers={"User-Agent": "Nokturno"}, method="POST")
        with mock.patch("urllib.request.urlopen", fake):
            servers.urlopen(req, timeout=5)
        self.assertEqual(videno, {"metoda": "POST", "data": b'{"a":1}', "agent": "Nokturno"})

    def test_odpoved_serveru_druhou_adresu_nezkousi(self):
        volano = []

        def fake(req, timeout=None):
            url = req.full_url if isinstance(req, urllib.request.Request) else req
            volano.append(url)
            raise urllib.error.HTTPError(url, 429, "moc dotazů", {}, None)

        with mock.patch("urllib.request.urlopen", fake):
            with self.assertRaises(urllib.error.HTTPError):
                servers.urlopen(self.url(servers.BASES[0]), timeout=5)
        self.assertEqual(len(volano), 1)

    def test_selhana_adresa_se_chvili_preskakuje(self):
        servers.note_fail(servers.BASES[0])
        poradi = servers.variants(self.url(servers.BASES[0]))
        self.assertTrue(poradi[0].startswith(servers.BASES[1]))
        self.assertFalse(servers.alive(servers.BASES[0]))

    def test_uspech_znamku_o_vypadku_smaze(self):
        for base in servers.BASES:
            servers.note_fail(base)
        with mock.patch("urllib.request.urlopen", lambda req, timeout=None: Resp("x")):
            servers.urlopen(self.url(servers.BASES[0]), timeout=5)
        self.assertTrue(servers.alive(servers.BASES[0]), "adresa, která odpověděla, je zase živá")
        self.assertFalse(servers.alive(servers.BASES[1]), "o druhé se nic nového neví")

    def test_cizi_adresa_se_neprepisuje(self):
        cizi = "https://api.themoviedb.org/3/movie/603"
        self.assertEqual(servers.variants(cizi), [cizi])

    def test_selze_li_vse_vyleti_posledni_chyba(self):
        def fake(req, timeout=None):
            raise urllib.error.URLError("mrtvo")

        with mock.patch("urllib.request.urlopen", fake):
            with self.assertRaises(urllib.error.URLError):
                servers.urlopen(self.url(servers.BASES[0]), timeout=5)


if __name__ == "__main__":
    unittest.main()
