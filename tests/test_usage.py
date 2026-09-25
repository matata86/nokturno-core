import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from nokturno_core.lib import usage                 # noqa: E402
from nokturno_core.lib.store import Store           # noqa: E402


class TestUsage(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())

    def test_pocitadla_casy_a_vraceni(self):
        usage.count(self.store, "search")
        usage.count(self.store, "search")
        usage.count(self.store, "play_ok:ws")
        usage.count(self.store, "Nesmysl s mezerou")    # neprojde klíčem
        for s in range(1, 11):
            usage.timing(self.store, s)
        taken = usage.take(self.store)
        out = usage.payload(taken)
        self.assertEqual(out["cnt"], {"search": 2, "play_ok:ws": 1})
        self.assertEqual(out["load"], {"n": 10, "p50": 6.0, "p95": 10.0})
        self.assertEqual(usage.payload(usage.take(self.store)), {})   # vynulováno
        usage.count(self.store, "search")
        usage.restore(self.store, taken)                # hlášení neodešlo
        again = usage.payload(usage.take(self.store))
        self.assertEqual(again["cnt"]["search"], 3)
        self.assertEqual(again["load"]["n"], 10)

    def test_funkce(self):
        usage.mark_feature(self.store, "syncwatch")
        self.assertEqual(usage.features(self.store), ["syncwatch"])


if __name__ == "__main__":
    unittest.main()
