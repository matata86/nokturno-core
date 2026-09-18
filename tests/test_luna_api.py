"""Rozpoznání serveru Luna, diagnostika nastavení a hledání v síti — bez sítě.

Chování Luny, proti kterému je to psané (ověřeno na 1.7.0): `/manifest.json` jde
bez tokenu a nese `luna.absolutecinema`, kdežto dotaz s tokenem vrátí manifest
i pro naprostý nesmysl — token se pozná až podle toho, jestli přijdou streamy.
"""
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import luna_api  # noqa: E402
from nokturno_core.lib.luna_api import (FAIL, OK, WARN, LunaError, diagnose,  # noqa: E402
                                        discover, normalize_base_url, server_info)

MANIFEST = {"id": "luna.absolutecinema.addon", "version": "1.7.0", "name": "Luna: Absolute Cinema"}
STREAM = {"streams": [{"name": "4K", "url": "http://192.168.1.10:7126/stream/l/AAA/"}]}
PRAZDNO = {"streams": []}


class Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body


class FakeNet:
    """Odpovídá podle cesty; `routes` je seznam (podřetězec, odpověď nebo výjimka)."""

    def __init__(self, *routes):
        self.routes = routes
        self.calls = []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.calls.append(url)
        for needle, answer in self.routes:
            if needle in url:
                if isinstance(answer, Exception):
                    raise answer
                return Resp(json.dumps(answer).encode())
        raise OSError("neobsloužená adresa: " + url)


def net(*routes):
    return mock.patch.object(luna_api.urllib.request, "urlopen", FakeNet(*routes))


class TestNormalizeBaseUrl(unittest.TestCase):
    def test_doplni_schema_i_port(self):
        self.assertEqual(normalize_base_url("192.168.1.10"), "http://192.168.1.10:7126")

    def test_nechá_vlastní_port(self):
        self.assertEqual(normalize_base_url("192.168.1.10:9000"), "http://192.168.1.10:9000")

    def test_orizne_cestu_a_lomitko(self):
        self.assertEqual(normalize_base_url("http://ha.local:7126/setup"), "http://ha.local:7126")

    def test_https_dostane_443(self):
        self.assertEqual(normalize_base_url("https://luna.example.com"), "https://luna.example.com:443")

    def test_prazdne(self):
        self.assertEqual(normalize_base_url(""), "")
        self.assertEqual(normalize_base_url(None), "")


class TestServerInfo(unittest.TestCase):
    def test_pozna_lunu_a_verzi(self):
        with net(("/manifest.json", MANIFEST)):
            self.assertEqual(server_info("192.168.1.10")["version"], "1.7.0")

    def test_cizi_server_na_stejnem_portu(self):
        with net(("/manifest.json", {"id": "org.stremio.other"})):
            with self.assertRaises(LunaError) as e:
                server_info("192.168.1.10")
            self.assertEqual(str(e.exception), "not_luna")

    def test_nedostupny(self):
        with net(("/manifest.json", OSError("spojení odmítnuto"))):
            with self.assertRaises(LunaError) as e:
                server_info("192.168.1.10")
            self.assertTrue(str(e.exception).startswith("unreachable"))


class TestDiagnose(unittest.TestCase):
    def test_nic_nevyplneno(self):
        self.assertEqual(diagnose("", "")["code"], "no_url")

    def test_server_neodpovida(self):
        with net(("/manifest.json", OSError("timed out"))):
            r = diagnose("192.168.1.10", "e1.abc")
        self.assertEqual((r["level"], r["code"]), (FAIL, "unreachable"))
        self.assertIn("timed out", r["detail"])

    def test_na_adrese_neco_jineho(self):
        with net(("/manifest.json", {"id": "jiny.addon"})):
            self.assertEqual(diagnose("192.168.1.10", "e1.abc")["code"], "not_luna")

    def test_chybi_token(self):
        with net(("/manifest.json", MANIFEST)):
            r = diagnose("192.168.1.10", "")
        self.assertEqual(r["code"], "no_token")
        self.assertEqual(r["version"], "1.7.0")   # server přitom běží — to je půlka odpovědi

    def test_token_neni_token(self):
        with net(("/manifest.json", MANIFEST)):
            self.assertEqual(diagnose("192.168.1.10", "muj token")["code"], "bad_token_format")

    def test_vse_v_poradku(self):
        with net(("/manifest.json", MANIFEST), ("/stream/", STREAM)):
            r = diagnose("192.168.1.10", "e1.abc")
        self.assertEqual((r["level"], r["code"]), (OK, "ok"))

    def test_token_vlozeny_jako_cela_instalacni_adresa(self):
        """Nejčastější vložení z `/setup` Luny — adresa i token v jednom poli."""
        with net(("/manifest.json", MANIFEST), ("/stream/", STREAM)):
            r = diagnose("", "http://192.168.1.10:7126/metadata/e1.abc/manifest.json")
        self.assertEqual(r["code"], "ok")
        self.assertEqual((r["base"], r["token"]), ("http://192.168.1.10:7126", "e1.abc"))

    def test_token_luna_neprijala(self):
        """Neplatný token: manifest projde, dotaz na streamy utne spojení."""
        with net(("/manifest.json", MANIFEST), ("/stream/", OSError("Remote end closed connection"))):
            r = diagnose("192.168.1.10", "e1.spatny", probe_ids=("tt1", "tt2"))
        self.assertEqual((r["level"], r["code"]), (FAIL, "bad_token"))

    def test_hlavni_zdroj_mlci_ale_hledani_najde(self):
        with net(("/manifest.json", MANIFEST), ("/search/", STREAM), ("/stream/", PRAZDNO)):
            r = diagnose("192.168.1.10", "e1.abc", probe_ids=("tt1",))
        self.assertEqual((r["level"], r["code"]), (WARN, "main_empty"))

    def test_luna_nenajde_vubec_nic(self):
        with net(("/manifest.json", MANIFEST), ("/stream/", PRAZDNO)):
            r = diagnose("192.168.1.10", "e1.abc", probe_ids=("tt1",))
        self.assertEqual((r["level"], r["code"]), (WARN, "no_streams"))

    def test_zkousi_vic_titulu_nez_to_vzda(self):
        """Hlavní zdroj Luny nemá všechno — jeden prázdný titul ještě nic neznamená."""
        fake = FakeNet(("/manifest.json", MANIFEST), ("tt0111161", PRAZDNO), ("tt0068646", STREAM))
        with mock.patch.object(luna_api.urllib.request, "urlopen", fake):
            r = diagnose("192.168.1.10", "e1.abc", probe_ids=("tt0111161", "tt0068646"))
        self.assertEqual(r["code"], "ok")

    def test_deep_false_se_streamu_neptá(self):
        fake = FakeNet(("/manifest.json", MANIFEST))
        with mock.patch.object(luna_api.urllib.request, "urlopen", fake):
            self.assertEqual(diagnose("192.168.1.10", "e1.abc", deep=False)["code"], "ok")
        self.assertEqual(len(fake.calls), 1)


class FakeSocket:
    """Otevřený port jen na vybraných adresách."""

    otevrene = ()

    def __init__(self, *a, **kw):
        self.host = None

    def settimeout(self, t):
        pass

    def connect_ex(self, addr):
        self.host = addr[0]
        return 0 if addr[0] in self.otevrene else 1

    def connect(self, addr):
        pass

    def getsockname(self):
        return ("192.168.1.55", 0)

    def close(self):
        pass


class TestDiscover(unittest.TestCase):
    def setUp(self):
        import socket
        self.socket_patch = mock.patch.object(socket, "socket", FakeSocket)
        self.socket_patch.start()
        self.addCleanup(self.socket_patch.stop)

    def test_najde_jen_skutecnou_lunu(self):
        """Na jedné adrese Luna, na druhé cizí web na stejném portu."""
        FakeSocket.otevrene = ("192.168.1.10", "192.168.1.20")
        with net(("192.168.1.10", MANIFEST), ("192.168.1.20", {"id": "neco.jineho"})):
            found = discover(subnet="192.168.1")
        self.assertEqual([f["url"] for f in found], ["http://192.168.1.10:7126"])
        self.assertEqual(found[0]["version"], "1.7.0")

    def test_nikde_nic(self):
        FakeSocket.otevrene = ()
        with net(("/manifest.json", MANIFEST)):
            self.assertEqual(discover(subnet="192.168.1"), [])

    def test_bez_podsite_nehleda(self):
        with mock.patch.object(luna_api, "local_subnet", lambda: ""):
            self.assertEqual(discover(), [])

    def test_hlasi_prubeh(self):
        FakeSocket.otevrene = ()
        kroky = []
        with net(("/manifest.json", MANIFEST)):
            discover(subnet="192.168.1", on_progress=lambda done, total: kroky.append((done, total)))
        self.assertEqual(kroky[-1], (254, 254))

    def test_prerusitelne(self):
        FakeSocket.otevrene = ("192.168.1.10",)
        with net(("192.168.1.10", MANIFEST)):
            self.assertEqual(discover(subnet="192.168.1", should_stop=lambda: True), [])


if __name__ == "__main__":
    unittest.main()
