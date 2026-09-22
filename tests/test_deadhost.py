"""Pauza pro hostitele, ze kterého nešlo stáhnout (2026-09-22).

Naměřeno živě: 18 z 54 serverů streamuj.tv nebralo spojení. Bez pauzy by se
u titulu s pěti streamy ze stejného mrtvého serveru čekalo pětkrát na timeout
místo jednou.
"""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import deadhost  # noqa: E402
from nokturno_core.lib.store import Store  # noqa: E402


class TestDeadhost(unittest.TestCase):
    def setUp(self):
        deadhost.clear()

    def test_host_of(self):
        self.assertEqual(deadhost.host_of("http://s42.streamuj.tv/f.mp4"), "s42.streamuj.tv")
        self.assertEqual(deadhost.host_of("https://S41.streamuj.tv/x"), "s41.streamuj.tv")
        self.assertEqual(deadhost.host_of("nesmysl"), "")
        self.assertFalse(deadhost.is_dead(""))

    def test_mark_dead_a_vyprseni(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertFalse(deadhost.is_dead("s42.streamuj.tv", store))
            from unittest import mock
            with mock.patch("time.time", return_value=1000.0):
                deadhost.mark_dead("s42.streamuj.tv", store)
                self.assertTrue(deadhost.is_dead("s42.streamuj.tv", store))
            with mock.patch("time.time", return_value=1000.0 + deadhost.COOLDOWN + 1):
                self.assertFalse(deadhost.is_dead("s42.streamuj.tv", store))

    def test_pauza_prezije_do_store_a_precte_ji_druha_instance(self):
        """Simuluje dva různé procesy (Kodi: plugin × služba) — druhá instance jádra
        vidí pauzu jen přes disk, ne přes paměť procesu."""
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            deadhost.mark_dead("s42.streamuj.tv", store)
            deadhost.clear()   # smaže jen paměť tohohle „procesu" (modulu), disk zůstává
            self.assertTrue(deadhost.is_dead("s42.streamuj.tv", store))

    def test_max_hosts_neroste_bez_konce(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            for i in range(deadhost.MAX_HOSTS + 20):
                deadhost.mark_dead(f"s{i}.streamuj.tv", store)
            data = store.load(deadhost.STORE, {})
            self.assertLessEqual(len(data), deadhost.MAX_HOSTS)


if __name__ == "__main__":
    unittest.main()
