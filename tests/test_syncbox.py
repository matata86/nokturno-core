"""Synchronizace přes slepý relay (`nokturno_core/lib/syncbox.py`) — bez sítě.

Relay se nahrazuje pamětí (`FakeRelay`), takže testy proženou celou cestu dvou
zařízení: zabalení stavu, nahrání, stažení cizího blobu a slití do vlastního
úložiště. Jestli server nedokáže do blobu vidět, se ověřuje tím, že se v něm
nehledá text — hledá se, že ho neotevře ani jiný kód.
"""
import base64
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

from nokturno_core.lib import syncbox  # noqa: E402
from nokturno_core.lib.store import Store  # noqa: E402
from nokturno_core.lib.sync import collect_changes
from nokturno_core.lib.syncbox import (  # noqa: E402
    CODE_LEN, Relay, SyncError, filter_circles, format_code, new_code,
    keys_for, normalize_code, sanitize, seal, sync_once, unseal, valid_code,
)

KOD = "NKT-8G4M-2QX7-VB9K-TRWP"


class Resp(io.BytesIO):
    """Odpověď `urlopen` jako kontextový manažer."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


class FakeRelay(object):
    """Relay v paměti: drží jeden blob na zařízení a nic o obsahu neví."""

    def __init__(self, max_devices=10):
        self.blobs = {}        # device_id -> (rev, blob)
        self.rev = 0
        self.open_until = 0
        self.max_devices = max_devices
        self.seen_groups = set()
        self.pushes = 0

    def urlopen(self, req, timeout=None):
        group = req.get_header("X-nokturno-group")
        device = req.get_header("X-nokturno-device")
        self.seen_groups.add(group)
        url = req.full_url
        if req.get_method() == "POST":
            self.open_until = time.time() + 1800
            return Resp(b"{}")
        if req.get_method() == "PUT":
            if device not in self.blobs:
                if time.time() > self.open_until:
                    raise urllib.error.HTTPError(url, 403, "zavřeno", {}, None)
                if len(self.blobs) >= self.max_devices:
                    raise urllib.error.HTTPError(url, 409, "plno", {}, None)
            self.rev += 1
            self.blobs[device] = (self.rev, req.data)
            self.pushes += 1
            return Resp(b"{}")
        if req.get_method() == "DELETE":
            self.blobs.pop(device, None)
            return Resp(b"{}")
        since = int(url.partition("since=")[2] or 0)
        cizi = [{"blob": base64.b64encode(blob).decode()} for dev, (rev, blob) in self.blobs.items()
                if dev != device and rev > since]
        return Resp(json.dumps({"rev": self.rev, "devices": cizi}).encode())


def store(tmp, name):
    cesta = os.path.join(tmp, name)
    os.makedirs(cesta, exist_ok=True)
    return Store(cesta)


class TestKod(unittest.TestCase):
    def test_novy_kod_ma_tvar_a_je_nahodny(self):
        kody = {new_code() for _ in range(20)}
        self.assertEqual(len(kody), 20)
        for kod in kody:
            self.assertTrue(valid_code(kod), kod)
            self.assertEqual(len(normalize_code(kod)), CODE_LEN)
            self.assertTrue(kod.startswith("NKT-"))

    def test_zamena_znaku_pri_opisu_z_televize(self):
        # člověk opíše O místo 0 a I nebo L místo 1
        self.assertEqual(normalize_code("NKT-0011-2345-6789-ABCD"),
                         normalize_code("nkt oOiL 2345 6789 abcd"))

    def test_spatny_kod_neprojde(self):
        for spatny in ("", "NKT-1234", "NKT-8G4M-2QX7-VB9K-TRW", KOD + "X", "NKT-8G4M-2QX7-VB9K-TRW!"):
            self.assertFalse(valid_code(spatny), spatny)
        with self.assertRaises(SyncError):
            keys_for("moc krátký")


class TestKlice(unittest.TestCase):
    def test_stejny_kod_stejne_klice(self):
        a, b = keys_for(KOD), keys_for(normalize_code(KOD).lower())
        self.assertEqual(a.ident, b.ident)
        self.assertEqual(a.enc, b.enc)

    def test_jiny_kod_jina_skupina(self):
        self.assertNotEqual(keys_for(KOD).ident, keys_for(new_code()).ident)

    def test_ident_neprozradi_kod_ani_klice(self):
        k = keys_for(KOD)
        self.assertEqual(len(k.ident), 32)
        self.assertNotIn(normalize_code(KOD), k.ident.upper())
        # z ident se nedá odvodit šifrovací klíč
        self.assertNotIn(k.ident.encode(), k.enc)


class TestObalka(unittest.TestCase):
    def setUp(self):
        self.keys = keys_for(KOD)
        self.data = {"watched": {"tt1": {"ts": 10, "playcount": 1}}, "device": "Obývák"}

    def test_zabalit_a_rozbalit(self):
        self.assertEqual(unseal(self.keys, seal(self.keys, self.data)), self.data)

    def test_blob_neobsahuje_otevreny_text(self):
        blob = seal(self.keys, {"watched": {"Pelíšky (1999)": {"ts": 1}}})
        self.assertNotIn("Pelíšky".encode("utf-8"), blob)
        self.assertNotIn(b"watched", blob)

    def test_stejny_stav_vypada_pokazde_jinak(self):
        self.assertNotEqual(seal(self.keys, self.data), seal(self.keys, self.data))

    def test_cizi_klic_neotevre(self):
        self.assertIsNone(unseal(keys_for(new_code()), seal(self.keys, self.data)))

    def test_zmeneny_blob_neprojde(self):
        blob = bytearray(seal(self.keys, self.data))
        blob[20] ^= 0xFF                      # jeden bit v šifrovaném textu
        self.assertIsNone(unseal(self.keys, bytes(blob)))
        self.assertIsNone(unseal(self.keys, b""))
        self.assertIsNone(unseal(self.keys, b"x" * 40))

    def test_velky_stav_se_vejde_do_limitu(self):
        # 5000 titulů je strop `WATCHED_MAX`; po gzipu musí zůstat pod limitem relaye
        velky = {"watched": {"tt%d" % i: {"ts": i, "playcount": 1, "pos": 12.5, "total": 5400.0}
                             for i in range(5000)}}
        self.assertLess(len(seal(self.keys, velky)), syncbox.MAX_BLOB)


class TestOkruhy(unittest.TestCase):
    def setUp(self):
        self.stav = {"watched": {"a": {"ts": 1}}, "favlog": {"b": {"ts": 2, "on": True}},
                     "histlog": {"c": {"ts": 3}}, "next_hidden": {"d": {"ts": 4, "ep": "e"}},
                     "items": {"a": {"title": "A"}}}

    def test_vypnuty_okruh_se_neposila(self):
        jen_watched = filter_circles(self.stav, ("watched",))
        self.assertEqual(set(jen_watched), {"watched", "next_hidden", "items"})
        self.assertNotIn("favlog", jen_watched)
        self.assertNotIn("histlog", jen_watched)

    def test_zadny_okruh_neposle_nic_ani_snimky(self):
        self.assertEqual(filter_circles(self.stav, ()), {})

    def test_neznamy_okruh_se_ignoruje(self):
        self.assertEqual(filter_circles(self.stav, ("vymyšlený",)), {})


class TestHodinyZBudoucnosti(unittest.TestCase):
    def test_zaznam_z_budoucnosti_se_zahodi(self):
        ted = int(time.time())
        stav = {"watched": {"dobry": {"ts": ted}, "rozbity": {"ts": ted + 10 * 365 * 86400}},
                "items": {"dobry": {"title": "A"}}}
        cisty = sanitize(stav, now=ted)
        self.assertIn("dobry", cisty["watched"])
        self.assertNotIn("rozbity", cisty["watched"])
        self.assertEqual(cisty["items"], stav["items"])   # snímky čas nemají


class TestVymena(unittest.TestCase):
    """Dvě zařízení proti relayi v paměti."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.obyvak = store(self.tmp, "obyvak")
        self.loznice = store(self.tmp, "loznice")
        self.relay = FakeRelay()
        self.patch = mock.patch("urllib.request.urlopen", side_effect=self.relay.urlopen)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        Relay(keys_for(KOD), "master").open_group()      # master otevře připojení

    def sync(self, st, **kw):
        return sync_once(st, KOD, **kw)

    def test_zhlednuto_dojde_na_druhy_box(self):
        self.obyvak.set_watched("tt100", True)
        ok, pushed, _, why = self.sync(self.obyvak, name="Obývák")
        self.assertTrue(ok, why)
        self.assertGreaterEqual(pushed, 1)

        ok, _, pulled, why = self.sync(self.loznice, name="Ložnice")
        self.assertTrue(ok, why)
        self.assertGreaterEqual(pulled, 1)
        self.assertTrue(self.loznice.watched("tt100"))

    def test_novejsi_zaznam_vyhrava_obema_smery(self):
        self.obyvak.set_resume("tt7", 100, 5400)
        self.sync(self.obyvak)
        self.sync(self.loznice)
        self.assertAlmostEqual(self.loznice.resume("tt7")[0], 100, places=0)

        time.sleep(1.1)                       # `ts` jsou celé sekundy
        self.loznice.set_resume("tt7", 2000, 5400)
        self.sync(self.loznice)
        self.sync(self.obyvak)
        self.assertAlmostEqual(self.obyvak.resume("tt7")[0], 2000, places=0)

    def test_muj_seznam_prenasi_i_odebrani(self):
        self.obyvak.toggle_favourite("tt55", {"title": "Film"})
        self.sync(self.obyvak)
        self.sync(self.loznice)
        self.assertTrue(self.loznice.is_favourite("tt55"))

        time.sleep(1.1)
        self.obyvak.toggle_favourite("tt55")          # odebráno
        self.sync(self.obyvak)
        self.sync(self.loznice)
        self.assertFalse(self.loznice.is_favourite("tt55"))

    def test_vypnuty_okruh_se_neprenese(self):
        self.obyvak.set_watched("tt1", True)
        self.obyvak.toggle_favourite("tt2", {"title": "B"})
        self.sync(self.obyvak, circles=("watched",))
        self.sync(self.loznice, circles=("watched",))
        self.assertTrue(self.loznice.watched("tt1"))
        self.assertFalse(self.loznice.is_favourite("tt2"))

    def test_beze_zmeny_se_nenahrava_znovu(self):
        self.obyvak.set_watched("tt1", True)
        self.sync(self.obyvak)
        prvni = self.relay.pushes
        self.sync(self.obyvak)
        self.assertEqual(self.relay.pushes, prvni, "stejný stav se nahrál podruhé")
        self.obyvak.set_watched("tt2", True)
        self.sync(self.obyvak)
        self.assertEqual(self.relay.pushes, prvni + 1)

    def test_cizi_skupina_se_tise_preskoci(self):
        self.obyvak.set_watched("tt1", True)
        self.sync(self.obyvak)
        # blob od někoho s jiným kódem v téže skupině (server je nerozliší)
        self.relay.blobs["vetrelec"] = (99, seal(keys_for(new_code()), {"watched": {"tt9": {"ts": 1}}}))
        ok, _, pulled, why = self.sync(self.loznice)
        self.assertTrue(ok, why)
        self.assertFalse(self.loznice.watched("tt9"))

    def test_server_vidi_jen_group_id(self):
        self.obyvak.set_watched("Pelíšky (1999)", True)
        self.sync(self.obyvak)
        self.assertEqual(self.relay.seen_groups, {keys_for(KOD).ident})
        self.assertNotIn(normalize_code(KOD), " ".join(self.relay.seen_groups).upper())
        blob = list(self.relay.blobs.values())[0][1]
        self.assertNotIn("Pelíšky".encode("utf-8"), blob)

    def test_kazde_zarizeni_ma_vlastni_id(self):
        self.sync(self.obyvak)
        self.sync(self.loznice)
        self.assertEqual(len(self.relay.blobs), 2)

    def test_zarizeni_si_drzi_stejne_id_napric_koly(self):
        """Id zařízení leží v témže souboru jako stav kola. Kdyby ho závěrečné
        uložení přepsalo, vyrobí si zařízení po každém kole novou identitu —
        v relayi přibývá osiřelý blob a skupina se zaplní na MAX_DEVICES."""
        self.obyvak.set_watched("tt1", True)
        self.sync(self.obyvak)
        prvni = self.obyvak.reload("syncbox", {}).get("device")
        self.assertTrue(prvni)

        self.obyvak.set_watched("tt2", True)
        self.sync(self.obyvak)
        self.assertEqual(self.obyvak.reload("syncbox", {}).get("device"), prvni)
        self.assertEqual(list(self.relay.blobs), [prvni], "přibyl blob pod novým id")

    def test_odhlaseni_po_kole_smaze_vlastni_blob(self):
        self.obyvak.set_watched("tt1", True)
        self.sync(self.obyvak)
        Relay(keys_for(KOD), syncbox.device_id(self.obyvak)).forget()
        self.assertEqual(self.relay.blobs, {})

    def test_spatny_kod_hlasi_chybu_a_nespadne(self):
        ok, _, _, why = sync_once(self.obyvak, "NKT-krátký")
        self.assertFalse(ok)
        self.assertTrue(why)
        self.assertEqual(self.obyvak.reload("syncbox", {}).get("last_error"), why)

    def test_vypadek_site_jen_zapise_chybu(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("síť spadla")):
            ok, _, _, why = self.sync(self.obyvak)
        self.assertFalse(ok)
        self.assertIn("síť", why)

    def test_zavrena_skupina_odmitne_nove_zarizeni(self):
        self.relay.open_until = 0
        ok, _, _, why = self.sync(self.loznice)
        self.assertFalse(ok)
        self.assertIn("nová zařízení", why)


if __name__ == "__main__":
    unittest.main()


class TestHomeAssistantJakoClen(unittest.TestCase):
    """HA je členem skupiny na relayi a zároveň středem pro Kodi v místní síti.

    Kodi mimo domácí síť (mobil) na `/api/nokturno/sync` nedosáhne, na relay ano.
    Aby byl most úplný, musí se změna přijatá z relaye rozeslat dál i Kodi, která
    chodí přes HA — a ta si berou jen záznamy novější než poslední výměna. Proto
    HA volá `sync_once(stamp=True)` a přijatým záznamům razí čas příjmu (`rts`).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.mobil = store(self.tmp, "mobil")
        self.ha = store(self.tmp, "ha")
        self.relay = FakeRelay()
        self.patch = mock.patch("urllib.request.urlopen", side_effect=self.relay.urlopen)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        Relay(keys_for(KOD), "master").open_group()

    def _stary_zaznam(self, key, stari=1000):
        """Mobil byl dlouho bez signálu: záznam vznikl dávno a na relay jde až teď."""
        self.mobil.set_watched(key, True)
        watched = self.mobil.reload("watched", {})
        watched[key]["ts"] = int(time.time()) - stari
        self.mobil.save("watched", watched)
        sync_once(self.mobil, KOD, name="Mobil")

    def test_prijata_zmena_se_posle_dal_kodi_pres_ha(self):
        self._stary_zaznam("tt900")
        posledni_vymena = int(time.time()) - 500   # HA si s Kodi vyměnilo data mezitím

        ok, _, pulled, why = sync_once(self.ha, KOD, name="Home Assistant", stamp=True)
        self.assertTrue(ok, why)
        self.assertGreaterEqual(pulled, 1)

        # bez `rts` by filtr `since` záznam přeskočil — vznikl před poslední výměnou
        self.assertIn("tt900", collect_changes(self.ha, posledni_vymena)["watched"])

    def test_bez_stampu_zustane_zaznam_stat(self):
        self._stary_zaznam("tt901")
        posledni_vymena = int(time.time()) - 500
        sync_once(self.ha, KOD, name="Kodi")       # Kodi `stamp` nepoužívá
        self.assertNotIn("tt901", collect_changes(self.ha, posledni_vymena)["watched"])
