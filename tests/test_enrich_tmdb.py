import unittest
from unittest import mock

from nokturno_core.lib import enrich


class FakeTmdb:
    def brief(self, ctype, imdb):
        return {"description": "Český popis", "imdbRating": 7.1, "genres": ["Drama"], "poster": "", "year": "2020"}


class TestEnrichTmdb(unittest.TestCase):
    def tearDown(self):
        enrich.shutdown_pool()

    def test_tmdb_ma_prednost_pred_cinemetou(self):
        metas = [{"name": "Duha", "imdb_id": "tt0000001"}]
        with mock.patch.object(enrich, "_cinemeta", side_effect=AssertionError("Cinemeta se volat nemá")):
            self.assertEqual(enrich.enrich(metas, tmdb=FakeTmdb(), deadline=5), 1)
        self.assertEqual(metas[0]["description"], "Český popis")
        self.assertEqual(metas[0]["genres"], ["Drama"])

    def test_bez_tmdb_cinemeta(self):
        metas = [{"name": "Duha", "imdb_id": "tt0000002"}]
        with mock.patch.object(enrich, "_cinemeta", return_value={"description": "English", "imdbRating": 6}):
            enrich.enrich(metas, deadline=5)
        self.assertEqual(metas[0]["description"], "English")


if __name__ == "__main__":
    unittest.main()
