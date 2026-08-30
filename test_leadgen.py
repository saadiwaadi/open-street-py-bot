import unittest
from unittest.mock import patch
import pandas as pd

from leadgen_backend import config, geocode, overpass_client, cleaning
from leadgen_backend.api import router, SearchRequest
from fastapi.testclient import TestClient
from fastapi import FastAPI


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(config.get_overpass_endpoint(), "https://overpass-api.de/api/interpreter")
        self.assertIn("LeadGenBackend", config.get_user_agent())
        self.assertEqual(config.get_chunk_size_degrees(), 0.5)
        self.assertIsNone(config.get_cors_origin())


class TestGeocode(unittest.TestCase):
    @patch("leadgen_backend.geocode._fetch")
    def test_geocode_success(self, mock_fetch):
        mock_fetch.return_value = {
            "boundingbox": ["52.34", "52.67", "13.08", "13.76"]
        }
        bbox = geocode.geocode("Germany", "Berlin")
        self.assertEqual(bbox, (52.34, 13.08, 52.67, 13.76))

    def test_geocode_empty_country(self):
        with self.assertRaises(ValueError):
            geocode.geocode("", "City")


class TestOverpassClient(unittest.TestCase):
    def test_chunk_bbox(self):
        bbox = (10.0, 20.0, 10.8, 20.8)
        chunks = overpass_client._chunk_bbox(bbox)
        self.assertGreater(len(chunks), 0)
        south, west, north, east = chunks[0]
        self.assertLessEqual(south, north)
        self.assertLessEqual(west, east)

    def test_resolve_category_tags(self):
        tags_rest = overpass_client._resolve_category_tags("Restaurants")
        self.assertTrue(len(tags_rest) > 0)
        tags_custom = overpass_client._resolve_category_tags("amenity=pharmacy", is_custom_category=True)
        self.assertEqual(tags_custom, [("amenity", "pharmacy")])


class TestCleaning(unittest.TestCase):
    def test_haversine_vectorized(self):
        import numpy as np
        dist = cleaning.haversine_vectorized(np.array([0.0]), np.array([0.0]), np.array([0.0]), np.array([0.0]))
        self.assertEqual(dist[0], 0.0)
        dist_bp = cleaning.haversine_vectorized(
            np.array([52.5200]), np.array([13.4050]),
            np.array([48.8566]), np.array([2.3522])
        )
        self.assertTrue(850000 < dist_bp[0] < 900000)

    def test_process_pois_and_deduplicate(self):
        raw_elements = [
            {
                "osm_id": 101,
                "osm_type": "node",
                "name": " Cafe Central ",
                "phone": "+49 30 123456",
                "website": "http://cafe-central.de",
                "address": "12 Main St",
                "category": "cafe",
                "lat": 52.5,
                "lon": 13.4
            },
            {
                "osm_id": 102,
                "osm_type": "node",
                "name": "Cafe Central",
                "phone": "030 123456",
                "website": "cafe-central.de",
                "address": "12 Main Street",
                "category": "cafe",
                "lat": 52.5001,
                "lon": 13.4001
            },
        ]
        # process_pois normalizes and deduplicates using deduplicate_fuzzy
        df_clean = cleaning.process_pois(raw_elements)
        self.assertEqual(len(df_clean), 1)
        self.assertEqual(df_clean.iloc[0]["name"].strip(), "Cafe Central")

        json_recs = cleaning.prepare_json_records(df_clean)
        self.assertEqual(len(json_recs), 1)
        self.assertIsInstance(json_recs[0]["osm_id"], int)


class TestApi(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    @patch("leadgen_backend.geocode.geocode")
    @patch("leadgen_backend.overpass_client.query_pois")
    def test_search_endpoint_get(self, mock_query, mock_geocode):
        mock_geocode.return_value = (52.0, 13.0, 53.0, 14.0)
        mock_query.return_value = (
            [
                {
                    "osm_id": 1,
                    "osm_type": "node",
                    "name": "Test Bakery",
                    "phone": "",
                    "website": "",
                    "address": "",
                    "category": "shop=bakery",
                    "lat": 52.5,
                    "lon": 13.5
                }
            ],
            False,
            False
        )
        response = self.client.get("/search?country=Germany&city=Berlin")
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertIn("results", json_data)
        self.assertEqual(len(json_data["results"]), 1)
        self.assertEqual(json_data["results"][0]["name"], "Test Bakery")

    @patch("leadgen_backend.geocode.geocode")
    @patch("leadgen_backend.overpass_client.query_pois")
    def test_search_endpoint_post(self, mock_query, mock_geocode):
        mock_geocode.return_value = (31.5, 74.3, 31.6, 74.4)
        mock_query.return_value = (
            [
                {
                    "osm_id": 5,
                    "osm_type": "node",
                    "name": "Lahore Diner",
                    "phone": "",
                    "website": "",
                    "address": "",
                    "category": "amenity=restaurant",
                    "lat": 31.55,
                    "lon": 74.35
                }
            ],
            False,
            False
        )
        payload = {
            "country": "Pakistan",
            "city": "Lahore",
            "category": "Restaurants",
            "is_custom_category": False,
            "limit_mode": "capped",
            "limit_value": 50,
            "output_mode": "session"
        }
        response = self.client.post("/search", json=payload)
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertEqual(json_data["status"], "success")
        self.assertEqual(len(json_data["results"]), 1)
        self.assertEqual(json_data["results"][0]["name"], "Lahore Diner")


if __name__ == "__main__":
    unittest.main()
