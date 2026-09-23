"""Klient pro vlastní žebříček Nokturna (`nokturno_core/lib/trend_api.py`) — bez sítě,
`urllib.request.urlopen` se podstrkuje stejně jako u ostatních API klientů."""
import json
import time
import pathlib
import sys
import unittest
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import servers  # noqa: E402
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
    """Cache na disku v paměti — `peek_cached` vrací jen to, co tam `cached_if` uložil."""

    def __init__(self):
        self.volani = []
        self.data = {}        # klíč → (uloženo, data)

    def cached(self, key, ttl, loader):
        self.volani.append((key, ttl))
        data = loader()
        self.data[key] = (time.time(), data)
        return data

    def peek_cached(self, key, ttl):
        kdy, data = self.data.get(key, (0, None))
        return data if data is not None and time.time() - kdy < ttl else None

    def cached_if(self, key, ttl, loader, ok=bool, fresh=False):
        self.volani.append((key, ttl))
        data = loader()
        if ok(data):
            self.data[key] = (time.time(), data)
        return data

    def zestarni(self, key, o_kolik):
        kdy, data = self.data[key]
        self.data[key] = (kdy - o_kolik, data)


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
        self.assertNotIn("nokturno:trending:movie", cache.data, "prázdno se nezapamatuje")

    def test_po_vypadku_se_dashboard_chvili_nevola(self):
        """Nález 30 z auditu: bez značky se čekalo `TIMEOUT` při každém otevření
        menu Filmy i Seriály — stejný vzor jako `dash_api.DOWN_KEY`."""
        pokusy = []

        def urlopen(req, timeout=None):
            pokusy.append(req.full_url)
            raise OSError("connection refused")

        cache = FakeCache()
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            TrendApi(cache=cache).catalog("movie", CATALOG_ID)
            TrendApi(cache=cache).catalog("series", CATALOG_ID)
            TrendApi(cache=cache).catalog("movie", CATALOG_ID)
        # jedno kolo = pokus na každou známou adresu serveru (`lib/servers.py`)
        self.assertEqual(len(pokusy), len(servers.BASES),
                         f"po prvním výpadku se nemá volat znovu: {pokusy}")

    def test_pri_vypadku_se_ukazou_starsi_data(self):
        body = json.dumps({"items": [{"id": "tt1", "name": "X"}]}).encode("utf-8")
        cache = FakeCache()
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None: Resp(body)):
            TrendApi(cache=cache).catalog("movie", CATALOG_ID)

        def urlopen(req, timeout=None):
            raise OSError("connection refused")

        cache.zestarni("nokturno:trending:movie", 9 * 3600)   # cache prošla, ale data pořád máme
        with mock.patch.object(urllib.request, "urlopen", urlopen):
            items = TrendApi(cache=cache).catalog("movie", CATALOG_ID)
        self.assertEqual([i["id"] for i in items], ["tt1"], "radši starší žebříček než prázdné menu")

    def test_cache_dostane_spravny_klic_a_ttl(self):
        body = json.dumps({"items": [{"id": "tt1", "name": "X"}]}).encode("utf-8")
        cache = FakeCache()
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None: Resp(body)):
            TrendApi(cache=cache).catalog("movie", CATALOG_ID)
        self.assertEqual(cache.volani, [("nokturno:trending:movie", 8 * 3600)])
        self.assertEqual(cache.data["nokturno:trending:movie"][1], [{"id": "tt1", "name": "X"}])

    def test_chyba_pri_parsovani_je_trendapierror(self):
        with mock.patch.object(urllib.request, "urlopen", lambda req, timeout=None: Resp(b"neplatny json")):
            with self.assertRaises(TrendApiError):
                TrendApi()._get("movie")


if __name__ == "__main__":
    unittest.main()
