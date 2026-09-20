"""Přenos nastavení do dalšího zařízení a kryptografie pod ním (`sealbox`)."""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nokturno_core.lib import sealbox, transfer  # noqa: E402

SCHEMA = [
    {"id": "ws", "label": "WebShare", "fields": [
        {"id": "ws_enabled", "type": "bool"},
        {"id": "ws_username", "type": "text"},
        {"id": "ws_password", "type": "password"},
        {"type": "heading", "label": "Úložiště 1"},
        {"type": "info", "label": "Návod"},
        {"id": "luna_check_action", "type": "action"},
    ]},
    {"id": "sync", "label": "Synchronizace", "fields": [
        {"id": "sync_enabled", "type": "bool"},
        {"id": "sync_key", "type": "text"},
        {"id": "download_dir", "type": "text"},
        {"id": "cz_enabled", "type": "bool"},
    ]},
]

VALUES = {"ws_enabled": "true", "ws_username": "martin", "ws_password": "tajne",
          "sync_enabled": "true", "sync_key": "abcdef", "download_dir": "/storage/stahovani",
          "cz_enabled": "true"}


class TestSealbox(unittest.TestCase):
    def test_kod_ma_tvar_a_projde_normalizaci(self):
        code = sealbox.new_code(8)
        self.assertTrue(code.startswith("NKT-"))
        self.assertEqual(code.count("-"), 2)
        self.assertTrue(sealbox.valid_code(code, 8))
        self.assertFalse(sealbox.valid_code(code, 16))

    def test_opis_z_televize_snese_zamenene_znaky(self):
        raw = "ABCDEFGH"
        keys = sealbox.keys_for(raw, b"s", 8)
        # člověk opisující z obrazovky plete O s nulou a I/L s jedničkou
        self.assertEqual(sealbox.normalize_code("nkt-0bcd-efgh"), "0BCDEFGH")
        self.assertEqual(sealbox.normalize_code("OBCD EFGH"), "0BCDEFGH")
        self.assertEqual(sealbox.keys_for("ABCD-EFGH", b"s", 8).ident, keys.ident)

    def test_kod_mimo_abecedu_neprojde(self):
        self.assertFalse(sealbox.valid_code("NKT-ABCD-EFG@", 8))
        self.assertFalse(sealbox.valid_code("NKT-ABCD-EFG", 8))     # krátký kód
        # U v abecedě není, ale kdo ho opíše z obrazovky, myslel V
        self.assertTrue(sealbox.valid_code("NKT-ABCD-EFGU", 8))
        self.assertEqual(sealbox.keys_for("ABCDEFGU", b"s", 8).ident,
                         sealbox.keys_for("ABCDEFGV", b"s", 8).ident)
        with self.assertRaises(sealbox.SealError):
            sealbox.Keys("NKT-ABC", b"s", 8)

    def test_ruzna_sul_ruzne_klice(self):
        a = sealbox.keys_for("ABCDEFGH", b"nokturno-sync-v1", 8)
        b = sealbox.keys_for("ABCDEFGH", b"nokturno-transfer-v1", 8)
        self.assertNotEqual(a.ident, b.ident)
        self.assertNotEqual(a.enc, b.enc)
        # blob jednoho účelu nejde podstrčit druhému
        self.assertIsNone(sealbox.unseal(b, sealbox.seal(a, {"x": 1})))

    def test_seal_unseal_a_nahodna_nonce(self):
        keys = sealbox.keys_for("ABCDEFGH", b"s", 8)
        payload = {"a": "ěščřž", "b": [1, 2, 3]}
        first, second = sealbox.seal(keys, payload), sealbox.seal(keys, payload)
        self.assertNotEqual(first, second)          # nonce je pokaždé jiná
        self.assertEqual(sealbox.unseal(keys, first), payload)
        self.assertEqual(sealbox.unseal(keys, second), payload)

    def test_poskozeny_blob_neprojde(self):
        keys = sealbox.keys_for("ABCDEFGH", b"s", 8)
        blob = bytearray(sealbox.seal(keys, {"a": 1}))
        blob[20] ^= 0x01
        self.assertIsNone(sealbox.unseal(keys, bytes(blob)))
        self.assertIsNone(sealbox.unseal(keys, b""))
        self.assertIsNone(sealbox.unseal(keys, b"\x00" * 47))

    def test_cizi_kod_nic_neprecte(self):
        mine = sealbox.keys_for("ABCDEFGH", b"s", 8)
        theirs = sealbox.keys_for("ABCDEFGJ", b"s", 8)
        self.assertIsNone(sealbox.unseal(theirs, sealbox.seal(mine, {"heslo": "tajne"})))

    def test_sifrovani_neni_holy_gzip(self):
        keys = sealbox.keys_for("ABCDEFGH", b"s", 8)
        blob = sealbox.seal(keys, {"ws_password": "tajneheslo"})
        self.assertNotIn(b"tajneheslo", blob)
        self.assertNotIn(b"\x1f\x8b", blob[:32])   # gzip hlavička není vidět


class TestExport(unittest.TestCase):
    def test_seznam_vznika_ze_schematu(self):
        names = transfer.exportable(SCHEMA)
        self.assertIn("ws_username", names)
        self.assertIn("cz_enabled", names)
        # podnadpis, návod ani tlačítko nemají hodnotu
        self.assertNotIn(None, names)
        self.assertNotIn("luna_check_action", names)

    def test_stroj_specificke_se_neprenasi(self):
        names = transfer.exportable(SCHEMA)
        for name in ("download_dir", "sync_enabled", "sync_key"):
            self.assertNotIn(name, names)

    def test_pack_zahodi_deny_i_kdyz_je_v_hodnotach(self):
        payload = transfer.pack(SCHEMA, VALUES, source="Kodi 6.2.0", now=1700000000)
        self.assertEqual(payload["format"], transfer.FORMAT)
        self.assertEqual(payload["created"], 1700000000)
        self.assertEqual(payload["settings"]["ws_password"], "tajne")
        self.assertNotIn("sync_key", payload["settings"])
        self.assertNotIn("download_dir", payload["settings"])

    def test_zapnuty_cztor_jde_jen_jako_priznak(self):
        payload = transfer.pack(SCHEMA, VALUES)
        self.assertTrue(payload["flags"]["cztor"])
        self.assertNotIn("trakt", payload["flags"])
        # žádný token CZtoru v obsahu — ten se použitím mění a kopie by odhlásila původní Kodi
        self.assertNotIn("cz_token", json.dumps(payload))

    def test_chybejici_hodnota_se_preskoci(self):
        payload = transfer.pack(SCHEMA, {"ws_username": "martin"})
        self.assertEqual(list(payload["settings"]), ["ws_username"])


class TestImport(unittest.TestCase):
    def setUp(self):
        self.code = transfer.new_code()
        self.payload = transfer.pack(SCHEMA, VALUES, source="Kodi 6.2.0")

    def test_round_trip(self):
        blob = transfer.export_bytes(self.code, self.payload)
        self.assertEqual(transfer.import_bytes(self.code, blob), self.payload)

    def test_spatny_kod(self):
        blob = transfer.export_bytes(self.code, self.payload)
        with self.assertRaises(transfer.TransferError):
            transfer.import_bytes(transfer.new_code(), blob)
        with self.assertRaises(transfer.TransferError):
            transfer.import_bytes("NKT-ABC", blob)

    def test_novejsi_format_se_odmitne(self):
        payload = dict(self.payload, format=transfer.FORMAT + 1)
        blob = transfer.export_bytes(self.code, payload)
        with self.assertRaises(transfer.TransferError):
            transfer.import_bytes(self.code, blob)

    def test_starsi_format_projde(self):
        payload = dict(self.payload, format=transfer.FORMAT - 1)
        blob = transfer.export_bytes(self.code, payload)
        self.assertEqual(transfer.import_bytes(self.code, blob)["format"], transfer.FORMAT - 1)

    def test_nesmyslny_obsah(self):
        blob = transfer.export_bytes(self.code, {"format": 1, "settings": "ne"})
        with self.assertRaises(transfer.TransferError):
            transfer.import_bytes(self.code, blob)


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.payload = transfer.pack(SCHEMA, VALUES)
        self.known = transfer.exportable(SCHEMA)

    def test_rozdeli_na_zmeny_a_shodne(self):
        plan = transfer.plan(self.payload, {"ws_username": "jiny", "ws_password": "tajne",
                                            "ws_enabled": "true", "cz_enabled": "false"}, self.known)
        self.assertEqual(plan.changes, {"cz_enabled": "true", "ws_username": "martin"})
        self.assertEqual(sorted(plan.same), ["ws_enabled", "ws_password"])
        self.assertEqual(len(plan), 2)
        self.assertFalse(plan.empty())

    def test_neznama_polozka_se_nezapise(self):
        payload = dict(self.payload, settings=dict(self.payload["settings"], z_budoucnosti="1"))
        plan = transfer.plan(payload, {}, self.known)
        self.assertEqual(plan.unknown, ["z_budoucnosti"])
        self.assertNotIn("z_budoucnosti", plan.changes)

    def test_deny_neprojde_ani_z_podvrzeneho_prenosu(self):
        payload = dict(self.payload, settings=dict(self.payload["settings"],
                                                   sync_key="cizi", download_dir="/tmp"))
        plan = transfer.plan(payload, {"sync_key": "moje"}, self.known + ["sync_key", "download_dir"])
        self.assertEqual(plan.blocked, ["download_dir", "sync_key"])
        self.assertNotIn("sync_key", plan.changes)

    def test_priznaky_projdou_do_planu(self):
        plan = transfer.plan(self.payload, {}, self.known)
        self.assertEqual(plan.flags, ["cztor"])

    def test_bez_known_se_bere_soucasne_nastaveni(self):
        plan = transfer.plan(self.payload, {"ws_username": "jiny"})
        self.assertEqual(list(plan.changes), ["ws_username"])
        self.assertIn("ws_enabled", plan.unknown)


class _Handler(BaseHTTPRequestHandler):
    blobs = {}
    seen = []

    def log_message(self, *args):
        pass

    def _ident(self):
        return self.headers.get("X-Nokturno-Transfer") or ""

    def do_POST(self):
        blob = self.rfile.read(int(self.headers.get("content-length") or 0))
        self.seen.append(("POST", self._ident(), len(blob)))
        self.blobs[self._ident()] = blob
        body = json.dumps({"ok": True, "ttl": 900}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.seen.append(("GET", self._ident(), 0))
        blob = self.blobs.pop(self._ident(), None)   # jedno vyzvednutí
        if blob is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)


class TestRelay(unittest.TestCase):
    """Proti opravdovému HTTP serveru — falešný `urlopen` by neukázal, co letí po drátě."""

    @classmethod
    def setUpClass(cls):
        _Handler.blobs, _Handler.seen = {}, []
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.url = "http://127.0.0.1:%d/transfer" % cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_posle_a_vyzvedne(self):
        code, ttl = transfer.send(SCHEMA, VALUES, source="Kodi 6.2.0", base_url=self.url)
        self.assertEqual(ttl, 900)
        payload = transfer.receive(code, base_url=self.url)
        self.assertEqual(payload["settings"]["ws_username"], "martin")

    def test_kod_nikdy_neopusti_zarizeni(self):
        code, _ = transfer.send(SCHEMA, VALUES, base_url=self.url)
        raw = sealbox.normalize_code(code)
        for method, ident, size in _Handler.seen:
            self.assertNotIn(raw, ident)
            self.assertEqual(len(ident), 32)
        # na server jde jen `ident`, a ten je jednosměrný
        self.assertEqual(transfer.ident(code), _Handler.seen[-1][1])

    def test_druhe_vyzvednuti_uz_nic_nenajde(self):
        code, _ = transfer.send(SCHEMA, VALUES, base_url=self.url)
        transfer.receive(code, base_url=self.url)
        with self.assertRaises(transfer.TransferError):
            transfer.receive(code, base_url=self.url)

    def test_spatny_tvar_kodu_se_na_server_vubec_nezepta(self):
        before = len(_Handler.seen)
        with self.assertRaises(transfer.TransferError):
            transfer.receive("NKT-XXXX", base_url=self.url)
        self.assertEqual(len(_Handler.seen), before)

    def test_telo_je_neprehledna_binarka(self):
        transfer.send(SCHEMA, VALUES, base_url=self.url)
        blob = list(_Handler.blobs.values())[-1]
        self.assertNotIn(b"tajne", blob)
        self.assertNotIn(b"martin", blob)


if __name__ == "__main__":
    unittest.main()
