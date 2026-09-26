"""Okruhy `settings` a `accounts` — sdílení voleb doplňku a přihlášení.

Nastavení nejsou ve `Store`, takže je hostitel posílá dovnitř a zapisuje zpátky
sám (`setsync.py`). Testy jedou přes celé kolo dvou zařízení proti relayi
v paměti, aby se ověřilo i to, co se do blobu doopravdy dostane.
"""
import base64
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import setsync, syncbox  # noqa: E402
from nokturno_core.lib.store import Store  # noqa: E402
from tests.test_syncbox import FakeRelay, store  # noqa: E402

KOD = "NKT-8G4M-2QX7-VB9K-TRWP"
VSE = ("watched", "favourites", "history", "settings", "accounts")


class Zarizeni(object):
    """Kodi s vlastním profilem a vlastním `settings.xml` v paměti."""

    def __init__(self, tmp, jmeno, hodnoty, circles=VSE):
        self.store = store(tmp, jmeno)
        self.hodnoty = dict(hodnoty)
        self.circles = circles
        self.zapsano = []

    def _zapis(self, zmeny):
        self.hodnoty.update(zmeny)
        self.zapsano.append(dict(zmeny))

    def kolo(self, relay):
        with mock.patch.object(syncbox.urllib.request, "urlopen", relay.urlopen):
            return syncbox.sync_once(self.store, KOD, circles=self.circles, name="test",
                                     settings=self.hodnoty, on_settings=self._zapis)


class TestRozdeleni(unittest.TestCase):
    def test_hesla_patri_k_uctum_volby_k_nastaveni(self):
        self.assertEqual(setsync.circle_of("ws_password"), "accounts")
        self.assertEqual(setsync.circle_of("token"), "accounts", "token Luny je přihlášení")
        self.assertEqual(setsync.circle_of("dav1_url"), "accounts")
        self.assertEqual(setsync.circle_of("pref_lang"), "settings")
        self.assertEqual(setsync.circle_of("stream_layout"), "settings")

    def test_prehrajto_je_ucet(self):
        # heslo Přehraj.to šlo do 8.4.0~beta16 okruhem nastavení, tedy i s vypnutými účty
        self.assertEqual(setsync.circle_of("pt_email"), "accounts")
        self.assertEqual(setsync.circle_of("pt_password"), "accounts")
        self.assertEqual(setsync.circle_of("pt_enabled"), "settings")

    def test_vlastni_nastaveni_synchronizace_se_nesdili(self):
        for klic in ("sync_enabled", "sync_mode", "sync_code", "sync_watched",
                     "sync_settings", "sync_accounts", "sync_watchlist", "download_dir", "install_id"):
            self.assertIsNone(setsync.circle_of(klic), klic)


class TestKolo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.relay = FakeRelay()
        self.relay.open_until = time.time() + 1800
        self.hodnoty = {"pref_lang": "1", "ws_username": "jan", "ws_password": "tajne",
                        "download_dir": "/storage/a", "sync_code": KOD}

    def _obe(self, circles=VSE, druhe=None):
        a = Zarizeni(self.tmp.name, "a", self.hodnoty, circles)
        b = Zarizeni(self.tmp.name, "b", druhe if druhe is not None else
                     {k: "" for k in self.hodnoty}, circles)
        return a, b

    def test_nastaveni_i_ucty_dojdou_na_druhe_kodi(self):
        a, b = self._obe()
        a.kolo(self.relay)
        b.kolo(self.relay)
        self.assertEqual(b.hodnoty["pref_lang"], "1")
        self.assertEqual(b.hodnoty["ws_password"], "tajne")
        self.assertEqual(b.zapsano[-1].get("ws_username"), "jan")

    def test_vypnuty_okruh_uctu_nechá_hesla_doma(self):
        a, b = self._obe(circles=("settings",))
        a.kolo(self.relay)
        b.kolo(self.relay)
        self.assertEqual(b.hodnoty["pref_lang"], "1")
        self.assertEqual(b.hodnoty["ws_password"], "", "heslo patří do okruhu accounts")

    def test_deny_se_neposila_ani_pri_obou_okruzich(self):
        a, _ = self._obe()
        a.kolo(self.relay)
        blob = list(self.relay.blobs.values())[0][1]
        data = syncbox.unseal(syncbox.keys_for(KOD), blob)
        poslano = set((data.get("setlog") or {})) | set((data.get("acclog") or {}))
        self.assertNotIn("download_dir", poslano)
        self.assertNotIn("sync_code", poslano)

    def test_novejsi_zmena_vyhrava(self):
        a, b = self._obe()
        a.kolo(self.relay)
        b.kolo(self.relay)
        b.hodnoty["pref_lang"] = "3"      # uživatel to přepnul na druhé televizi
        pozdeji = time.time() + 60         # LWW rozhoduje podle sekund, stejně jako u zhlédnuto
        with mock.patch.object(setsync.time, "time", lambda: pozdeji):
            b.kolo(self.relay)
        a.kolo(self.relay)
        self.assertEqual(a.hodnoty["pref_lang"], "3")
        self.assertEqual(a.zapsano[-1], {"pref_lang": "3"})

    def test_beze_zmeny_se_nic_nezapisuje(self):
        a, b = self._obe()
        a.kolo(self.relay)
        b.kolo(self.relay)
        b.zapsano.clear()
        b.kolo(self.relay)
        a.kolo(self.relay)
        self.assertEqual(b.zapsano, [], "stejná hodnota není změna")

    def test_nezname_nastaveni_se_nezapisuje(self):
        """Starší doplněk nemá cizí klíč ve `values` — nesmí si ho uložit a pak
        ho posílat dál jako svůj."""
        a = Zarizeni(self.tmp.name, "a", dict(self.hodnoty, novinka="1"))
        b = Zarizeni(self.tmp.name, "b", {k: "" for k in self.hodnoty})
        a.kolo(self.relay)
        b.kolo(self.relay)
        self.assertNotIn("novinka", b.hodnoty)

    def test_bez_hodnot_se_okruhy_chovaji_jako_vypnute(self):
        a = Zarizeni(self.tmp.name, "a", self.hodnoty)
        with mock.patch.object(syncbox.urllib.request, "urlopen", self.relay.urlopen):
            syncbox.sync_once(a.store, KOD, circles=VSE, name="bez nastavení")
        blob = list(self.relay.blobs.values())[0][1]
        data = syncbox.unseal(syncbox.keys_for(KOD), blob)
        self.assertNotIn("setlog", data)
        self.assertNotIn("acclog", data)


if __name__ == "__main__":
    unittest.main()
