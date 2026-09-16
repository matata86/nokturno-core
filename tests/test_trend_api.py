"""Klient pro vlastní žebříček Nokturna (`nokturno_core/lib/trend_api.py`) — bez sítě,
`urllib.request.urlopen` se podstrkuje stejně jako u ostatních API klientů."""
import json
import pathlib
import sys
import unittest
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib.trend_api import CATALOG_ID, TrendApi, TrendApiError  # noqa: E402


class Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


class FakeCache:
    def __init__(self):
        self.volani = []

    def cached(self, key, ttl, loader):
        self.volani.append((key, ttl))
        return loader()


class TestTrendApi(unittest.TestCase):
    def test_catalogs_vraci_jednu_polozku(self):
        cats = TrendApi().catalogs("movie")
        self.assertEqual([c["id"] for c in cats], [CATALOG_ID])

    def test_spatny_cid_hledani_a_stranka_vraci_prazdno(self):
        api = TrendApi()
        self.assertEqual(api.catalog("movie", "jiny"), [])
        self.assertEqual(api.catalog("movie", CATALOG_ID, search="cokoli"), [])
        self.assertEqual(api.catalog("movie", CATALOG_ID, skip=20), [])

    def test_stahne_a_doplni_type_a_title(self):
        body = json.dumps({"items": [
            {"id": "tt123", "name": "Matrix", "year": "1999", "poster": "p.jpg", "background": "", "description": ""},
        ]}).encode("utf-8")
        volane_url = {}

        def urlopen(req, timeout=None):
            volane_url["url"] = req.full_url
            return Resp(body)

        with mock.patch.object(urllib.request, "urlopen", urlopen):
            items = TrendApi().catalog("series", CATALOG_ID)
        self.assertIn("kind=series", volane_url["url"])
        self.assertEqual(items, [{"id": "tt123", "name": "Matrix", "year": "1999", "poster": "p.jpg",
                                   "background": "", "description": "", "type": "series", "_title": "Matrix"}])

    def test_polozky_bez_id_vypadnou(self):
        body = json.dumps({"items": [{"name": "Bez id"}, {"id": "tt1", "name": "S id"}]}).encode("utf-8")
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None: Resp(body)):
            items = TrendApi().catalog("movie", CATALOG_ID)
        self.assertEqual([i["id"] for i in items], ["tt1"])

    def test_vypadek_site_vrati_prazdny_seznam_necachuje_se(self):
        def urlopen(req, timeout=None):
            raise OSError("connection refused")

        cache = FakeCache()
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            items = TrendApi(cache=cache).catalog("movie", CATALOG_ID)
        self.assertEqual(items, [])

    def test_cache_dostane_spravny_klic_a_ttl(self):
        body = json.dumps({"items": [{"id": "tt1", "name": "X"}]}).encode("utf-8")
        cache = FakeCache()
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None: Resp(body)):
            TrendApi(cache=cache).catalog("movie", CATALOG_ID)
        self.assertEqual(cache.volani, [("nokturno:trending:movie", 8 * 3600)])

    def test_chyba_pri_parsovani_je_trendapierror(self):
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None: Resp(b"neplatny json")):
            with self.assertRaises(TrendApiError):
                TrendApi()._get("movie")


if __name__ == "__main__":
    unittest.main()
