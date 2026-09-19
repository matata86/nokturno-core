"""Hlášení o pádech (`nokturno_core/lib/crash.py`) — bez sítě, `urlopen` se podstrkuje."""
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import crash  # noqa: E402
from nokturno_core.lib.crash import CRASH_URL, CrashReporter, build_report, scrub, short_path  # noqa: E402

ID = "a" * 32


def vyhod(zprava="rozbité"):
    """Výjimka se skutečným tracebackem z tohohle souboru."""
    try:
        {}["klic"]
    except KeyError:
        try:
            raise ValueError(zprava)
        except ValueError as e:
            return e


class Resp:
    def __init__(self, code=200):
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return b"{}"

    def getcode(self):
        return self.code


class TestScrub(unittest.TestCase):
    def test_adresa_bez_cesty_a_parametru(self):
        text = scrub("chyba https://luna.example.cz:7126/tajnytoken123/manifest.json?x=1 konec")
        self.assertIn("https://luna.example.cz:7126/…", text)
        self.assertNotIn("tajnytoken123", text)

    def test_heslo_v_adrese_a_ip(self):
        text = scrub("dav://jan:heslo@192.168.1.10:5005/Filmy")
        self.assertNotIn("heslo", text)
        self.assertNotIn("jan", text)
        self.assertNotIn("192.168", text)

    def test_plugin_adresa_jen_s_akci(self):
        text = scrub("plugin://plugin.video.nokturno/?action=play&url=ws%3Aabc&name=Matrix")
        self.assertEqual(text, "plugin://plugin.video.nokturno/?action=play")

    def test_klice_a_hodnoty(self):
        text = scrub('login failed: token=abcd1234 password: "tajne" {"wst": "XyZ9"}')
        for tajne in ("abcd1234", "tajne", "XyZ9"):
            self.assertNotIn(tajne, text)

    def test_email_domovska_slozka_a_dlouhy_token(self):
        text = scrub("jan.novak@seznam.cz /home/jan/.kodi C:\\Users\\Jan\\AppData a1b2c3d4e5f6g7h8i9j0k1l2m3")
        self.assertNotIn("novak", text)
        self.assertNotIn("/home/jan", text)
        self.assertNotIn("Users\\Jan", text)
        self.assertNotIn("a1b2c3d4e5f6", text)

    def test_basic_bearer_a_ipv6(self):
        """Audit 2026-09-19: `Basic <base64 bez číslice>` a `Bearer …` procházely, IPv6 taky."""
        text = scrub("'Authorization': 'Basic dGFqbmVoZXNsbw==' Bearer eyJhbGciOiJIUzI1NiJ9.abc "
                     "klient 2a09:bac1:1da0:10::1f:b9 a fe80::1 v 20:03:59 spadl")
        self.assertNotIn("dGFqbmVoZXNsbw", text)
        self.assertNotIn("eyJhbGci", text)
        self.assertNotIn("2a09:bac1", text)
        self.assertNotIn("fe80::1", text)
        self.assertIn("20:03:59", text, "čas není IPv6")
        self.assertIn("Bearer ***", text)

    def test_nastaveni_stremia_v_ceste(self):
        self.assertEqual(scrub("GET /c/eyJ3cyI6InVzZXIifQ/stream/movie/tt1.json"),
                         "GET /c/<nastavení>/stream/movie/tt1.json")

    def test_obycejny_text_zustane(self):
        text = "KeyError: 'series' v list_episodes (verze 5.2.10~beta4)"
        self.assertEqual(scrub(text), text)


class TestReport(unittest.TestCase):
    def test_cesty_zkracene_a_vlastni_ramec(self):
        self.assertEqual(short_path("/storage/emulated/0/Android/data/org.xbmc.kodi/files/.kodi/addons/"
                                    "plugin.video.nokturno/default.py"), "plugin.video.nokturno/default.py")
        self.assertEqual(short_path("/usr/lib/python3.8/json/decoder.py"), "decoder.py")

    def test_hlaseni(self):
        with mock.patch.object(crash, "_OWN_MARKERS", ("test_crash.py",)):
            r = build_report(vyhod("heslo=tajne"), ID, "kodi", "5.2.10", platform="Android", kodi="21.2",
                             action="streams", log_lines=["x"] * 200 + ["token=abc"], now=100)
        self.assertEqual((r["type"], r["product"], r["action"], r["ts"]), ("ValueError", "kodi", "streams", 100))
        self.assertRegex(r["fp"], r"^[0-9a-f]{12}$")
        self.assertNotIn("tajne", r["message"] + r["traceback"])
        self.assertIn("test_crash.py:vyhod:", r["where"])
        self.assertIn('File "test_crash.py"', r["traceback"])
        self.assertIn("raise ValueError(zprava)", r["traceback"])
        self.assertEqual(len(r["log"].splitlines()), crash.LOG_LINES)
        self.assertNotIn("abc", r["log"])

    def test_otisk_nezavisi_na_zprave_ani_radku(self):
        a = build_report(vyhod("jedna"), ID, "kodi", "1")
        b = build_report(vyhod("dva"), ID, "kodi", "2")
        self.assertEqual(a["fp"], b["fp"])
        try:
            raise TypeError("jiný typ")
        except TypeError as e:
            c = build_report(e, ID, "kodi", "1")
        self.assertNotEqual(a["fp"], c["fp"])

    def test_vyjimka_bez_tracebacku(self):
        r = build_report(RuntimeError("nikdy nevyhozena"), ID, "stremio", "5.2.8")
        self.assertEqual(r["where"], "")
        self.assertIn("RuntimeError: nikdy nevyhozena", r["traceback"])


class TestFronta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rep = CrashReporter(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def soubory(self):
        return self.rep.pending()

    def test_stejny_pad_jednou_za_verzi(self):
        now = int(time.time())
        self.assertTrue(self.rep.capture(vyhod(), ID, "kodi", "1.0", now=now))
        self.assertFalse(self.rep.capture(vyhod(), ID, "kodi", "1.0", now=now + 1))
        self.assertTrue(self.rep.capture(vyhod(), ID, "kodi", "1.1", now=now + 2))
        self.assertEqual(len(self.soubory()), 2)

    def test_denni_limit(self):
        now = int(time.time())
        for i in range(crash.MAX_PER_DAY):
            self.assertTrue(self.rep.capture(vyhod(), ID, "kodi", f"v{i}", now=now))
        self.assertFalse(self.rep.capture(vyhod(), ID, "kodi", "dalsi", now=now))
        self.assertTrue(self.rep.capture(vyhod(), ID, "kodi", "zitra", now=now + 86400))

    def test_capture_nikdy_nevyhodi(self):
        with mock.patch.object(crash, "build_report", side_effect=RuntimeError("x")):
            self.assertFalse(self.rep.capture(vyhod(), ID, "kodi", "1"))

    def test_stare_a_prebyvajici_pryc(self):
        now = int(time.time())
        os.makedirs(self.rep.queue_dir)
        for i in range(crash.QUEUE_MAX + 5):
            pathlib.Path(self.rep.queue_dir, f"{now - 100 + i}-{i:012x}.json").write_text("{}")
        pathlib.Path(self.rep.queue_dir, f"{now - crash.QUEUE_MAX_AGE - 10}-stary.json").write_text("{}")
        zbyva = self.rep.pending(now)
        self.assertEqual(len(zbyva), crash.QUEUE_MAX)
        self.assertEqual(len(os.listdir(self.rep.queue_dir)), crash.QUEUE_MAX)

    def test_flush_odesle_a_smaze(self):
        self.rep.capture(vyhod(), ID, "kodi", "1", now=int(time.time()))
        with mock.patch("urllib.request.urlopen", return_value=Resp(200)) as uo:
            self.assertEqual(self.rep.flush(), (1, 0))
        req = uo.call_args[0][0]
        self.assertEqual(req.full_url, CRASH_URL)
        self.assertEqual(json.loads(req.data)["id"], ID)
        self.assertEqual(self.soubory(), [])

    def test_flush_bez_site_nechava(self):
        self.rep.capture(vyhod(), ID, "kodi", "1", now=int(time.time()))
        with mock.patch("urllib.request.urlopen", side_effect=OSError("síť")):
            self.assertEqual(self.rep.flush(), (0, 1))
        err = urllib.error.HTTPError(CRASH_URL, 503, "x", {}, io.BytesIO())
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertEqual(self.rep.flush(), (0, 1))
        self.assertEqual(len(self.soubory()), 1)

    def test_flush_odmitnute_smaze(self):
        self.rep.capture(vyhod(), ID, "kodi", "1", now=int(time.time()))
        err = urllib.error.HTTPError(CRASH_URL, 400, "x", {}, io.BytesIO())
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertEqual(self.rep.flush(), (0, 0))
        self.assertEqual(self.soubory(), [])


if __name__ == "__main__":
    unittest.main()
