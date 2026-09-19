import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from nokturno_core import Engine    # noqa: E402


class TestWrongLength(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.engine = Engine({}, self.tmp)

    def test_film_pod_nazvem_epizody_se_vyradi(self):
        streams = [
            {"url": "hs:1", "_duration": 2780},
            {"url": "hs:2", "_duration": 7476},
            {"url": "hs:3", "_duration": 0},
        ]
        kept = self.engine._drop_wrong_length(streams, {"runtime": "46"})
        self.assertEqual([s["url"] for s in kept], ["hs:1", "hs:3"])

    def test_dvojdil_a_krátká_epizoda_zustava(self):
        streams = [{"url": "a", "_duration": 75 * 60}]
        self.assertEqual(self.engine._drop_wrong_length(streams, {"runtime": "30"}), streams)

    def test_bez_stopaze_epizody_se_nic_nevyrazuje(self):
        streams = [{"url": "hs:2", "_duration": 7476}]
        self.assertEqual(self.engine._drop_wrong_length(streams, {}), streams)


if __name__ == "__main__":
    unittest.main()
