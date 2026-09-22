"""Stav účtů napříč zdroji — bez sítě.

Hlídá tři věci, na kterých tahle funkce stojí: čtení stavu nesmí sáhnout na síť
(menu v Kodi ho dělá při každém otevření), HellSpy se nesmí ptát nikdy (dvakrát
si tím doplněk přivodil blokaci 429) a pauza po 429 musí přežít přechod mezi
procesy, protože v Kodi je plugin jiný interpret než služba.
"""
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import urllib.error  # noqa: E402

from nokturno_core import engine as engine_mod  # noqa: E402
from nokturno_core.lib import accounts, hellspy_api  # noqa: E402
from nokturno_core.lib.cztor_api import CztorError  # noqa: E402
from nokturno_core.lib.fastshare_api import FastshareError  # noqa: E402
from nokturno_core.lib.sledujteto_api import SledujtetoError  # noqa: E402
from nokturno_core.lib.storage_api import StorageError  # noqa: E402
from nokturno_core.lib.webshare_api import WebshareApiError, WebshareError  # noqa: E402
from nokturno_core import Engine  # noqa: E402
from nokturno_core.lib.store import Store  # noqa: E402


class FakeWs:
    def __init__(self, **status):
        self.status = status
        self.volani = 0

    def account_status(self):
        self.volani += 1
        return self.status


class TestJednotliveZdroje(unittest.TestCase):
    def test_webshare_vip_s_rezervou(self):
        rec = accounts.webshare(FakeWs(vip=True, days=40, until="2026-12-20 16:58:10"))
        self.assertEqual((rec["level"], rec["code"]), (accounts.OK, "vip"))
        self.assertEqual(rec["detail"]["until"], "2026-12-20")   # bez času, do hlášky patří datum

    def test_webshare_konci_brzy(self):
        rec = accounts.webshare(FakeWs(vip=True, days=3, until="2026-09-23 10:00:00"))
        self.assertEqual((rec["level"], rec["code"], rec["detail"]["days"]), (accounts.WARN, "expires_soon", 3))

    def test_webshare_vyprselo(self):
        rec = accounts.webshare(FakeWs(vip=True, days=0, until=""))
        self.assertEqual((rec["level"], rec["code"]), (accounts.FAIL, "expired"))

    def test_webshare_bez_vip(self):
        # účet bez VIP hledá, ale stahuje pár kB/s — pro uživatele stejně nepoužitelné
        rec = accounts.webshare(FakeWs(vip=False, days=0, until=""))
        self.assertEqual((rec["level"], rec["code"]), (accounts.WARN, "free"))

    def test_fastshare_kredit(self):
        api = mock.Mock(account=lambda: {"unlimited": False, "credit_mb": 5120})
        rec = accounts.fastshare(api)
        self.assertEqual((rec["level"], rec["code"], rec["detail"]["gb"]), (accounts.OK, "credit", 5.0))

    def test_fastshare_dochazi_a_dosel(self):
        maly = accounts.fastshare(mock.Mock(account=lambda: {"unlimited": False, "credit_mb": 300}))
        self.assertEqual((maly["level"], maly["code"]), (accounts.WARN, "credit"))
        prazdny = accounts.fastshare(mock.Mock(account=lambda: {"unlimited": False, "credit_mb": 0}))
        self.assertEqual((prazdny["level"], prazdny["code"]), (accounts.FAIL, "no_credit"))

    def test_fastshare_neomezene(self):
        rec = accounts.fastshare(mock.Mock(account=lambda: {"unlimited": True}))
        self.assertEqual((rec["level"], rec["code"]), (accounts.OK, "unlimited"))

    def test_cztor_nesparovany(self):
        rec = accounts.cztor(mock.Mock(paired=lambda: False))
        self.assertEqual((rec["level"], rec["code"]), (accounts.FAIL, "not_paired"))

    def test_cztor_bez_site_bere_ulozeny_ucet(self):
        client = mock.Mock(paired=lambda: True,
                           account=lambda: {"plan": "Basic", "active": True, "valid_until": "2099-01-01"})
        client.profile.side_effect = AssertionError("deep=False se nesmí ptát po síti")
        rec = accounts.cztor(client, deep=False)
        self.assertEqual((rec["level"], rec["code"], rec["detail"]["plan"]), (accounts.OK, "ok", "Basic"))

    def test_cztor_predplatne_uz_probehlo(self):
        client = mock.Mock(paired=lambda: True,
                           account=lambda: {"plan": "Basic", "active": True, "valid_until": "2000-01-01"})
        rec = accounts.cztor(client, deep=False)
        # aktivní příznak ze session může být starý — rozhoduje datum
        self.assertEqual((rec["level"], rec["code"]), (accounts.FAIL, "expired"))

    def test_cztor_konci_brzy(self):
        za_tri_dny = time.strftime("%Y-%m-%d", time.localtime(time.time() + 3 * 86400))
        client = mock.Mock(paired=lambda: True,
                           account=lambda: {"plan": "Basic", "active": True, "valid_until": za_tri_dny})
        rec = accounts.cztor(client, deep=False)
        self.assertEqual((rec["level"], rec["code"], rec["detail"]["days"]), (accounts.WARN, "expires_soon", 3))

    def test_sledujteto_bez_premium(self):
        rec = accounts.sledujteto(mock.Mock(me=lambda: {"is_premium": False}))
        self.assertEqual((rec["level"], rec["code"]), (accounts.WARN, "no_premium"))
        dobry = accounts.sledujteto(mock.Mock(me=lambda: {"is_premium": True}))
        self.assertEqual((dobry["level"], dobry["code"]), (accounts.OK, "premium"))


class TestHellspyPauza(unittest.TestCase):
    """HellSpy je jediný zdroj, kterého se stav účtu nikdy neptá po síti."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(self.dir.name)
        hellspy_api._blocked_until = 0.0
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(setattr, hellspy_api, "_blocked_until", 0.0)

    def test_bez_pauzy_je_ok(self):
        rec = accounts.hellspy(self.store)
        self.assertEqual((rec["level"], rec["code"]), (accounts.OK, "ok"))

    def test_pauza_se_hlasi_v_minutach(self):
        hellspy_api._block(self.store)
        rec = accounts.hellspy(self.store)
        self.assertEqual((rec["level"], rec["code"]), (accounts.WARN, "paused"))
        self.assertEqual(rec["detail"]["minutes"], 10)

    def test_pauza_prezije_prechod_mezi_procesy(self):
        # plugin v Kodi zaznamená 429, služba na pozadí je jiný interpret
        hellspy_api._block(self.store)
        hellspy_api._blocked_until = 0.0            # nový proces začíná s prázdnou pamětí
        self.assertGreater(hellspy_api.blocked_for(self.store), 0)
        self.assertEqual(hellspy_api.blocked_for(), 0.0)   # a bez úložiště o ní neví

    def test_bez_uloziste_se_nespadne(self):
        hellspy_api._block(None)
        self.assertGreater(hellspy_api.blocked_for(None), 0)


class TestSlozeni(unittest.TestCase):
    def test_nenastaveny_zdroj_je_off(self):
        rows = accounts.compose({}, {"webshare": False, "hellspy": False})
        self.assertTrue(all(r["level"] == accounts.OFF for r in rows))
        self.assertEqual([r["source"] for r in rows], list(accounts.SOURCES))

    def test_nastaveny_bez_zaznamu_je_neznamy_ne_chyba(self):
        # čerstvá instalace: obnova na pozadí ještě neproběhla, menu nemá co hlásit
        rows = {r["source"]: r for r in accounts.compose({}, {"webshare": True})}
        self.assertEqual((rows["webshare"]["level"], rows["webshare"]["code"]), (accounts.OFF, "unknown"))
        self.assertEqual(accounts.problems(list(rows.values())), [])

    def test_stary_zaznam_se_hlasi_dal_jen_oznaceny(self):
        saved = {"webshare": {"level": accounts.FAIL, "code": "expired", "detail": {}, "ts": time.time() - 14 * 3600}}
        row = accounts.compose(saved, {"webshare": True})[accounts.SOURCES.index("webshare")]
        self.assertEqual(row["code"], "expired")
        self.assertTrue(row["stale"])

    def test_davno_neoverovany_zaznam_uz_neplati(self):
        saved = {"webshare": {"level": accounts.FAIL, "code": "expired", "detail": {}, "ts": time.time() - 90 * 3600}}
        row = accounts.compose(saved, {"webshare": True})[accounts.SOURCES.index("webshare")]
        self.assertEqual(row["code"], "unknown")

    def test_problemy_radi_nejzavaznejsi_napred(self):
        now = time.time()
        saved = {"hellspy": {"level": accounts.WARN, "code": "paused", "detail": {}, "ts": now},
                 "webshare": {"level": accounts.FAIL, "code": "expired", "detail": {}, "ts": now},
                 "luna": {"level": accounts.OK, "code": "ok", "detail": {}, "ts": now}}
        rows = accounts.compose(saved, {"hellspy": True, "webshare": True, "luna": True})
        self.assertEqual([r["source"] for r in accounts.problems(rows)], ["webshare", "hellspy"])


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        hellspy_api._blocked_until = 0.0
        self.addCleanup(setattr, hellspy_api, "_blocked_until", 0.0)

    def test_cteni_stavu_nesaha_na_sit(self):
        """Menu v Kodi volá `accounts()` při každém otevření — jediné čekání na
        odpověď by ho zpomalilo z 0,9 s na vteřiny."""
        engine = Engine({"ws_username": "kdosi", "ws_password": "x", "hs_enabled": True,
                         "st_email": "a@b.cz", "st_password": "x", "cz_enabled": True,
                         "luna_token": "e1.neco", "luna_url": "192.168.1.10:7126"}, self.dir.name)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("síť")), \
             mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("síť")):
            rows = engine.accounts()
        self.assertEqual([r["source"] for r in rows], list(accounts.SOURCES))
        self.assertEqual(engine.account_problems(), [])   # nic zjištěného = nic k hlášení

    def test_obnova_ulozi_a_dalsi_cteni_uz_je_z_disku(self):
        engine = Engine({"ws_username": "kdosi", "ws_password": "x"}, self.dir.name)
        with mock.patch.object(Engine, "ws", mock.PropertyMock(
                return_value=FakeWs(vip=True, days=2, until="2026-09-22 08:00:00"))):
            engine.refresh_accounts()
        # nový engine = nový proces; stav musí být na disku, ne v paměti
        dalsi = Engine({"ws_username": "kdosi", "ws_password": "x"}, self.dir.name)
        row = dalsi.account_problems()[0]
        self.assertEqual((row["source"], row["code"], row["detail"]["days"]), ("webshare", "expires_soon", 2))

    def test_obnova_nespadne_na_vypadku_zdroje(self):
        engine = Engine({"ws_username": "kdosi", "ws_password": "x", "hs_enabled": True}, self.dir.name)
        with mock.patch.object(Engine, "ws", mock.PropertyMock(side_effect=OSError("síť spadla"))):
            engine.refresh_accounts()
        kody = {r["source"]: r["code"] for r in engine.accounts()}
        self.assertEqual(kody["webshare"], "error")
        self.assertEqual(kody["hellspy"], "ok")   # ostatní zdroje výpadek jednoho nesmí strhnout

    def test_bez_site_se_ulozeny_stav_necha(self):
        """Mobil: Kodi na pozadí nebo hned po startu nemá síť, obnova skončí
        „neodpovídá" u všeho a v menu to pak stálo do další obnovy (nahlášeno
        z mobilu na `6.6.0~beta13`). Když nešlo dosáhnout na žádný síťový zdroj,
        je bez sítě zařízení — stav zůstává a zapíše se jen značka."""
        from nokturno_core.lib.webshare_api import WebshareError
        engine = Engine({"ws_username": "kdosi", "ws_password": "x", "hs_enabled": True,
                         "dav1_url": "http://nas/dav", "dav1_user": "u", "dav1_pass": "p"}, self.dir.name)
        with mock.patch.object(Engine, "ws", mock.PropertyMock(
                return_value=FakeWs(vip=True, days=90, until="2026-12-20 08:00:00"))), \
             mock.patch("nokturno_core.lib.accounts.storage", return_value=accounts._zaznam(accounts.OK, "ok")):
            engine.refresh_accounts()
        sitova = WebshareError("x"); sitova.__cause__ = urllib.error.URLError("no route")
        with mock.patch.object(Engine, "ws", mock.PropertyMock(return_value=None)), \
             mock.patch.object(engine, "ws_error", sitova), \
             mock.patch("nokturno_core.lib.accounts.storage", side_effect=StorageError("no route")):
            engine.refresh_accounts()
        kody = {r["source"]: r["code"] for r in engine.accounts()}
        self.assertEqual((kody["webshare"], kody["storage"]), ("vip", "ok"))
        self.assertTrue(engine.offline_recently())

    def test_jeden_nedostupny_zdroj_se_ulozi(self):
        """Neodpovídá-li jen jeden a ostatní ano, je to jeho výpadek, ne naše síť."""
        engine = Engine({"ws_username": "kdosi", "ws_password": "x",
                         "dav1_url": "http://nas/dav", "dav1_user": "u", "dav1_pass": "p"}, self.dir.name)
        with mock.patch.object(Engine, "ws", mock.PropertyMock(
                return_value=FakeWs(vip=True, days=90, until="2026-12-20 08:00:00"))), \
             mock.patch("nokturno_core.lib.accounts.storage", side_effect=StorageError("no route")):
            engine.refresh_accounts()
        kody = {r["source"]: r["code"] for r in engine.accounts()}
        self.assertEqual((kody["webshare"], kody["storage"]), ("vip", "unreachable"))
        self.assertFalse(engine.offline_recently())

    def test_vypadek_pri_hledani_neprepise_overeny_stav(self):
        """`ws` selže na síti při jednom hledání — ověřené VIP zůstává; odmítnuté
        heslo naopak přepíše hned (uživatel musí zasáhnout)."""
        from nokturno_core.lib.webshare_api import WebshareApiError, WebshareError
        engine = Engine({"ws_username": "kdosi", "ws_password": "x"}, self.dir.name)
        engine._note_account("webshare", accounts._zaznam(accounts.OK, "vip", days=90))
        engine._note_account("webshare", {"level": accounts.FAIL, "code": "unreachable", "detail": {}})
        self.assertEqual(engine.accounts()[accounts.SOURCES.index("webshare")]["code"], "vip")
        engine._note_account("webshare", {"level": accounts.FAIL, "code": "bad_login", "detail": {}})
        self.assertEqual(engine.accounts()[accounts.SOURCES.index("webshare")]["code"], "bad_login")

    def test_selhany_login_se_zapise_uz_pri_bezne_praci(self):
        """Za nula dotazů navíc: `ws` selže při hledání a menu to ví hned."""
        from nokturno_core.lib.webshare_api import WebshareApiError
        engine = Engine({"ws_username": "kdosi", "ws_password": "spatne"}, self.dir.name)
        with mock.patch("nokturno_core.engine.WebshareApi") as api:
            api.return_value.login.side_effect = WebshareApiError("login: Wrong password")
            self.assertIsNone(engine.ws)
        row = engine.account_problems()[0]
        self.assertEqual((row["source"], row["code"]), ("webshare", "bad_login"))

    def test_zapnuty_ale_nesparovany_cztor_se_hlasi(self):
        """Podle `sources()` je nespárovaný CZtor „vypnutý" — a `not_paired` by se
        tak nikdy neukázalo, přitom je to typické „zapnul jsem to a nejde to"."""
        engine = Engine({"cz_enabled": True}, self.dir.name)
        kody = {r["source"]: (r["level"], r["code"]) for r in engine.accounts()}
        self.assertNotEqual(kody["cztor"][1], "off")
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("síť")):
            engine.refresh_accounts(only=["cztor"])
        self.assertEqual(engine.account_problems()[0]["code"], "not_paired")

    def test_luna_s_adresou_bez_tokenu_se_hlasi(self):
        engine = Engine({"luna_url": "192.168.1.10:7126"}, self.dir.name)
        self.assertNotEqual(engine.accounts()[accounts.SOURCES.index("luna")]["code"], "off")

    def test_vypnuta_luna_se_nekontroluje(self):
        """Adresa Luny má v Kodi výchozí hodnotu, takže je vyplněná i u toho, kdo
        zdroj nikdy nezapnul — stav pak hlásil „běží, ale chybí token" (`6.6.0~beta11`)."""
        engine = Engine({"luna_enabled": False, "luna_url": "192.168.1.10:7126",
                         "luna_token": "abc"}, self.dir.name)
        self.assertEqual(engine.accounts()[accounts.SOURCES.index("luna")]["code"], "off")
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("síť")):
            engine.refresh_accounts(only=["luna"])

    def test_bez_prepinace_rozhoduji_vyplnene_udaje(self):
        """HA a Stremio `luna_enabled` neposílají — pro ně se nic nemění."""
        engine = Engine({"luna_url": "192.168.1.10:7126"}, self.dir.name)
        self.assertNotEqual(engine.accounts()[accounts.SOURCES.index("luna")]["code"], "off")

    def test_hellspy_se_v_obnove_nikdy_nepta_po_siti(self):
        """Právě opakovanými dotazy si doplněk dvakrát přivodil blokaci (6.0.2, 6.0.4)."""
        engine = Engine({"hs_enabled": True}, self.dir.name)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("síť")):
            engine.refresh_accounts()
        self.assertEqual(engine.accounts()[accounts.SOURCES.index("hellspy")]["code"], "ok")

    def test_pauza_hellspy_se_cte_ziva_ne_z_sestihodinoveho_zaznamu(self):
        engine = Engine({"hs_enabled": True}, self.dir.name)
        engine.refresh_accounts()                      # zapsáno „ok"
        hellspy_api._block(engine.store)               # 429 přišlo až potom
        hellspy_api._blocked_until = 0.0               # a v jiném procesu
        row = engine.accounts()[accounts.SOURCES.index("hellspy")]
        self.assertEqual((row["level"], row["code"]), (accounts.WARN, "paused"))


if __name__ == "__main__":
    unittest.main()


class TestKodUcetSelhani(unittest.TestCase):
    """Výpadek sítě není odmítnuté heslo. Uživatel měl 2026-09-21 v menu čtyři zdroje
    v chybě („nesedí jméno nebo heslo" u WebShare i Sledujteta), přitom „Ověřit zdroje"
    hned nato hlásilo všechno v pořádku: obnova na pozadí padla na vypnutou síť
    a zapsala se jako `bad_login`, a ten záznam pak platil dvanáct hodin."""

    def test_sit_je_unreachable(self):
        for chyba in (urllib.error.URLError("timed out"), OSError("Network is unreachable")):
            with self.subTest(chyba=type(chyba).__name__):
                try:
                    raise WebshareError("login: chyba") from chyba
                except WebshareError as err:
                    self.assertEqual(engine_mod._account_fail_code(err), "unreachable")

    def test_odmitnuty_ucet_je_bad_login(self):
        self.assertEqual(engine_mod._account_fail_code(WebshareApiError("login: Špatné heslo")), "bad_login")

    def test_sledujteto_podle_toho_kdo_odpovedel(self):
        self.assertEqual(engine_mod._account_fail_code(SledujtetoError("HTTP 401", status=401)), "bad_login")
        try:
            raise SledujtetoError("timed out") from urllib.error.URLError("timed out")
        except SledujtetoError as err:
            self.assertEqual(engine_mod._account_fail_code(err), "unreachable")

    def test_http_chyba_uvnitr_je_odpoved_serveru(self):
        """`HTTPError` je podtřída `URLError` — pořadí testů v `_server_odpovedel` rozhoduje."""
        try:
            raise FastshareError("login selhal") from urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        except FastshareError as err:
            self.assertEqual(engine_mod._account_fail_code(err), "bad_login")

    def test_bez_priciny_zustava_bad_login(self):
        self.assertEqual(engine_mod._account_fail_code(CztorError("účet neaktivní")), "bad_login")


class TestRucniUspaniZdroje(unittest.TestCase):
    """Menu „Uspat zdroj" (2026-09-22) — `accounts.pause()`/`paused_for()`/`paused()`,
    nezávislé na automatické pauze HellSpy po 429."""

    def test_zapsani_vyprseni_a_zruseni(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertEqual(accounts.paused_for(store, "webshare"), 0)
            accounts.pause(store, "webshare", 600)
            self.assertGreater(accounts.paused_for(store, "webshare"), 0)
            self.assertIn("webshare", accounts.paused(store))
            accounts.pause(store, "webshare", 0)
            self.assertEqual(accounts.paused_for(store, "webshare"), 0)
            self.assertNotIn("webshare", accounts.paused(store))

    def test_vyprsele_se_nehlasi(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            with mock.patch("time.time", return_value=1000.0):
                accounts.pause(store, "hellspy", 10)
            with mock.patch("time.time", return_value=1020.0):
                self.assertEqual(accounts.paused_for(store, "hellspy"), 0)
                self.assertEqual(accounts.paused(store), {})
