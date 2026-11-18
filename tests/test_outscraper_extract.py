import unittest
from urllib.parse import parse_qs, urlparse

from outscraper_extract.extract import DEFAULT_BBOX, Settings, max_billed, merge, parse_categories, search_all, to_place

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

    def test_matches_area_in_search_v3_address(self) -> None:
        raw = {"name": "Tienda Luna", "google_id": "g4", "address": "Cra. 2, Alameda del Río, Barranquilla"}
        self.assertEqual(merge({"q": [raw]}, DEFAULT_BBOX)[0].in_area_reason, "address")


class ToPlaceTest(unittest.TestCase):
    def test_reads_search_v3_field_names(self) -> None:
        raw = {"name": "Tienda Luna", "google_id": "g4", "website": "https://example.com/luna",
               "address": "Cra. 2, Barranquilla", "verified": True, "photos_count": 7}
        place = to_place(raw)
        self.assertEqual(place.website, "https://example.com/luna")
        self.assertEqual(place.full_address, "Cra. 2, Barranquilla")
        self.assertIs(place.verified, True)
        self.assertEqual(place.photos_count, 7)

    def test_falls_back_to_older_field_names(self) -> None:
        place = to_place({"name": "X", "site": "https://example.com", "full_address": "Cl. 1"})
        self.assertEqual((place.website, place.full_address), ("https://example.com", "Cl. 1"))


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

    def test_groups_queries_by_limit_and_keeps_query_order(self) -> None:
        sent: list[tuple[list[str], list[str]]] = []

        def fetch(url: str, headers: dict[str, str]) -> dict:
            params = parse_qs(urlparse(url).query)
            sent.append((params["query"], params["limit"]))
            return {"id": "r", "status": "Success", "data": [[] for _ in params["query"]]}

        settings = Settings(queries=["a", "b", "c"], limit=25, coordinates="@1,2,16z", limits={"b": 5})
        results = search_all(settings, "key", fetch=fetch, sleep=lambda _: None)
        self.assertEqual(sorted(sent), [(["a", "c"], ["25"]), (["b"], ["5"])])
        self.assertEqual(list(results), ["a", "b", "c"])


class CategoriesTest(unittest.TestCase):
    def test_parses_optional_limits(self) -> None:
        self.assertEqual(parse_categories(" gimnasio:5, restaurante ,salón de belleza:40"),
                         [("gimnasio", 5), ("restaurante", None), ("salón de belleza", 40)])

    def test_rejects_bad_limits(self) -> None:
        for text in ("gimnasio:0", "gimnasio:x", "gimnasio:", ":5", "a,,b"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_categories(text)

    def test_max_billed_sums_per_query_limits(self) -> None:
        settings = Settings(queries=["a", "b", "c"], limit=25, coordinates="", limits={"b": 5})
        self.assertEqual(max_billed(settings), 55)


if __name__ == "__main__":
    unittest.main()
