"""„Pro tebe“ — slučování doporučení a losování žánru (`nokturno_core/lib/foryou.py`).
Modul sám nikam nechodí, podstrkuje se mu `similar_fn`."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nokturno_core.lib import foryou  # noqa: E402


def rows(*keys):
    """Tvar `Store.recently_watched()` — [(klíč, záznam)]."""
    return [(k, {"playcount": 1, "ts": 1000 - i}) for i, k in enumerate(keys)]


def item(iid, name=""):
    return {"id": iid, "name": name or iid, "type": "movie"}


class TestSeedy(unittest.TestCase):
    def test_film_a_serial_se_nepletou(self):
        r = rows("tt0000001", "tt0000002:1:3", "tt0000003")
        self.assertEqual(foryou.seed_ids(r, "movie"), ["tt0000001", "tt0000003"])
        self.assertEqual(foryou.seed_ids(r, "series"), ["tt0000002"])

    def test_serial_je_jeden_vzor_ne_pet_dilu(self):
        r = rows("tt0000002:1:1", "tt0000002:1:2", "tt0000002:1:3", "tt0000009:2:1")
        self.assertEqual(foryou.seed_ids(r, "series"), ["tt0000002", "tt0000009"])

    def test_bez_imdb_id_se_doporucit_neda(self):
        r = rows("ws:abc", "hs:1:2", "sosacd_12345", "dav:0:/x.mkv", "tt0000001")
        self.assertEqual(foryou.seed_ids(r, "movie"), ["tt0000001"])

    def test_strop_poctu_vzoru(self):
        r = rows(*[f"tt000000{i}" for i in range(1, 9)])
        self.assertEqual(len(foryou.seed_ids(r, "movie")), foryou.SEEDS)
        self.assertEqual(len(foryou.seed_ids(r, "movie", limit=2)), 2)

    def test_znama_id_beru_z_vic_seznamu(self):
        self.assertEqual(foryou.known_ids(rows("tt0000001:1:2"), rows("tt0000002")),
                         {"tt0000001", "tt0000002"})


class TestDoporuceni(unittest.TestCase):
    def test_shoda_dvou_vzoru_predbehne_prvni_misto_u_jednoho(self):
        data = {"s1": [item("A"), item("S")], "s2": [item("B"), item("S")]}
        out = foryou.recommend(["s1", "s2"], lambda s: data[s])
        self.assertEqual([i["id"] for i in out], ["S", "A", "B"])

    def test_vzory_a_zhlednute_se_nedoporucuji(self):
        data = {"tt1": [item("tt1"), item("tt2"), item("tt3")]}
        out = foryou.recommend(["tt1"], lambda s: data[s], skip={"tt2"})
        self.assertEqual([i["id"] for i in out], ["tt3"])

    def test_protoze_jsi_videl_nese_id_prvniho_vzoru(self):
        data = {"s1": [item("A")], "s2": [item("A")]}
        out = foryou.recommend(["s1", "s2"], lambda s: data[s])
        self.assertEqual(out[0]["_because"], "s1")

    def test_stropy_per_seed_a_limit(self):
        data = {"s1": [item(f"a{i}") for i in range(30)], "s2": [item(f"b{i}") for i in range(30)]}
        out = foryou.recommend(["s1", "s2"], lambda s: data[s], per_seed=3, limit=4)
        self.assertEqual([i["id"] for i in out], ["a0", "b0", "a1", "b1"])

    def test_vypadek_zdroje_u_jednoho_vzoru_nevadi(self):
        out = foryou.recommend(["s1", "s2"], lambda s: [] if s == "s1" else [item("B")])
        self.assertEqual([i["id"] for i in out], ["B"])

    def test_polozka_bez_id_se_preskoci(self):
        out = foryou.recommend(["s1"], lambda s: [{"name": "bez id"}, item("B")])
        self.assertEqual([i["id"] for i in out], ["B"])


class TestZanry(unittest.TestCase):
    def test_pocty_zanru_ze_snimku(self):
        snaps = [{"genres": ["Komedie", "Drama"]}, {"genres": ["Komedie"]}, None, {}]
        self.assertEqual(foryou.genre_counts(snaps), {"Komedie": 2, "Drama": 1})

    def test_losovani_je_vazene_cetnosti(self):
        counts = {"Komedie": 3, "Drama": 1}
        self.assertEqual(foryou.pick_genre(counts, rnd=lambda: 0.0), "Komedie")
        self.assertEqual(foryou.pick_genre(counts, rnd=lambda: 0.7), "Komedie")
        self.assertEqual(foryou.pick_genre(counts, rnd=lambda: 0.99), "Drama")

    def test_bez_historie_zadny_zanr(self):
        self.assertIsNone(foryou.pick_genre({}))
        self.assertIsNone(foryou.pick_genre({"Komedie": 0}))


if __name__ == "__main__":
    unittest.main()
