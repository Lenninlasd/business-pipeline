import unittest
from urllib.parse import parse_qs, urlparse

from outscraper_extract.extract import DEFAULT_BBOX, Settings, merge, search_all

INSIDE = {"name": "Panadería Sol", "google_id": "g1", "latitude": 10.994, "longitude": -74.848, "phone": "+57 300"}
BY_ADDRESS = {"name": "Barbería Río", "google_id": "g2", "full_address": "Cl. 1, Alameda del Rio, Barranquilla"}
OUTSIDE = {"name": "Centro", "google_id": "g3", "latitude": 10.98, "longitude": -74.78}


class MergeTest(unittest.TestCase):
    def test_dedupes_and_classifies(self) -> None:
        results = {"panadería": [INSIDE, OUTSIDE], "negocios": [INSIDE, BY_ADDRESS], "bar": []}
        places = {p.google_id: p for p in merge(results, DEFAULT_BBOX)}
        self.assertEqual(len(places), 3)
        self.assertEqual(places["g1"].queries, ["panadería", "negocios"])
        self.assertEqual(places["g1"].in_area_reason, "bbox")
        self.assertEqual(places["g2"].in_area_reason, "address")  # accent-insensitive match
        self.assertIsNone(places["g3"].in_area_reason)


class SearchTest(unittest.TestCase):
    def test_batches_and_polls_until_success(self) -> None:
        calls: list[str] = []
        pending = {"id": "r1", "status": "Pending", "results_location": "https://api/requests/r1"}

        def fetch(url: str, headers: dict[str, str]) -> dict:
            calls.append(url)
            self.assertEqual(headers["X-API-KEY"], "key")
            if "search-v3" in url:
                return pending
            return {"id": "r1", "status": "Success", "data": [[INSIDE], [BY_ADDRESS]]}

        settings = Settings(queries=["a", "b", "c"], limit=10, coordinates="@1,2,16z", batch_size=2)
        results = search_all(settings, "key", fetch=fetch, sleep=lambda _: None)
        self.assertEqual(set(results), {"a", "b", "c"})
        search_urls = [u for u in calls if "search-v3" in u]
        self.assertEqual(len(search_urls), 2)
        self.assertEqual(parse_qs(urlparse(search_urls[0]).query)["query"], ["a", "b"])
        self.assertIn("https://api/requests/r1", calls)


if __name__ == "__main__":
    unittest.main()
