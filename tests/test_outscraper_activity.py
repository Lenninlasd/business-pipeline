import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from outscraper_extract.activity import activity_score, build_activity, fetch_details, parse_date

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


FULL = {"google_id": "g1", "name": "Tienda Luna", "business_status": "OPERATIONAL", "verified": True,
        "working_hours": {"lunes": "8-18"}, "phone": "+57 570 000 0000", "website": "https://example.com",
        "reviews": 12, "photos_count": 9}


class ParseDateTest(unittest.TestCase):
    def test_accepts_the_formats_outscraper_may_send(self) -> None:
        expected = datetime(2026, 3, 4, 13, 45, tzinfo=UTC)
        for value in (int(expected.timestamp()), str(int(expected.timestamp())), "03/04/2026 13:45:00",
                      "2026-03-04T13:45:00", "2026-03-04 13:45:00+00:00"):
            with self.subTest(value=value):
                self.assertEqual(parse_date(value), expected)

    def test_returns_none_for_missing_or_unknown(self) -> None:
        for value in (None, "", "ayer", [], {}):
            with self.subTest(value=value):
                self.assertIsNone(parse_date(value))


class ScoreTest(unittest.TestCase):
    def test_everything_recent_scores_near_100(self) -> None:
        score, signals = activity_score(FULL, days_ago(20), True, days_ago(40), NOW)
        self.assertEqual(score, 100)
        self.assertIn("review_90d", signals)

    def test_no_markers_scores_0(self) -> None:
        self.assertEqual(activity_score({"google_id": "g2", "name": "X"}, None, None, None, NOW), (0, []))

    def test_old_signals_score_less_than_recent_ones(self) -> None:
        recent, _ = activity_score(FULL, days_ago(30), False, days_ago(30), NOW)
        old, _ = activity_score(FULL, days_ago(600), False, days_ago(600), NOW)
        stale, _ = activity_score(FULL, days_ago(2000), False, days_ago(2000), NOW)
        self.assertGreater(recent, old)
        self.assertGreater(old, stale)

    def test_closed_permanently_is_0_and_temporarily_is_capped(self) -> None:
        closed = dict(FULL, business_status="CLOSED_PERMANENTLY")
        self.assertEqual(activity_score(closed, days_ago(5), True, days_ago(5), NOW), (0, ["closed_permanently"]))
        paused = dict(FULL, business_status="CLOSED_TEMPORARILY")
        score, signals = activity_score(paused, days_ago(5), True, days_ago(5), NOW)
        self.assertEqual(score, 20)
        self.assertIn("closed_temporarily", signals)


class FetchTest(unittest.TestCase):
    def test_batches_by_google_id_and_maps_answers(self) -> None:
        sent: list[dict[str, list[str]]] = []

        def fetch(url: str, headers: dict[str, str]) -> dict:
            params = parse_qs(urlparse(url).query)
            sent.append(params)
            self.assertIn("/maps/reviews-v3", url)
            # One item per query: a place dict, or a list holding it.
            data = [{"google_id": q, "reviews_data": []} if i % 2 else [{"google_id": q}]
                    for i, q in enumerate(params["query"])]
            return {"id": "r", "status": "Success", "data": data}

        answers = fetch_details("/maps/reviews-v3", ["a", "b", "c"], {"reviewsLimit": "1", "sort": "newest"},
                                "key", fetch=fetch, sleep=lambda _: None, batch_size=2)
        self.assertEqual([p["query"] for p in sent], [["a", "b"], ["c"]])
        self.assertEqual(sent[0]["reviewsLimit"], ["1"])
        self.assertEqual(sent[0]["sort"], ["newest"])
        self.assertEqual({k: v["google_id"] for k, v in answers.items()}, {"a": "a", "b": "b", "c": "c"})


class BuildActivityTest(unittest.TestCase):
    def test_picks_newest_review_and_downloads_photos(self) -> None:
        review_answer = {"reviews_data": [
            {"review_timestamp": int(days_ago(400).timestamp()), "owner_answer": None},
            {"review_timestamp": int(days_ago(10).timestamp()), "owner_answer": "Gracias"},
        ]}
        photo_answer = {"photos_data": [
            {"photo_url_big": "https://example.com/p1.jpg", "photo_date": "10/01/2026 10:00:00"},
            {"photo_url": "https://example.com/p2.jpg", "photo_date": "06/01/2025 10:00:00"},
            {"photo_date": "06/01/2025 10:00:00"},  # no URL: dropped
        ]}
        downloaded: list[tuple[str, Path]] = []

        def download(url: str, path: Path) -> None:
            downloaded.append((url, path))

        record = build_activity(FULL, review_answer, photo_answer, Path("out/photos"), download, NOW)
        self.assertEqual(record["last_review_date"], days_ago(10).date().isoformat())
        self.assertIs(record["owner_answered_last_review"], True)
        self.assertEqual(record["latest_photo_date"], "2026-10-01")
        self.assertEqual([p["url"] for p in record["photos"]],
                         ["https://example.com/p1.jpg", "https://example.com/p2.jpg"])
        name = hashlib.sha1(b"https://example.com/p1.jpg").hexdigest()[:12]
        self.assertEqual(downloaded[0], ("https://example.com/p1.jpg", Path(f"out/photos/g1/{name}.jpg")))
        self.assertEqual(record["photos"][0]["file"], f"out/photos/g1/{name}.jpg")
        self.assertEqual(record["activity_score"], 100)
        self.assertEqual(record["name"], "Tienda Luna")  # place fields are kept

    def test_street_view_photos_do_not_count_as_recent(self) -> None:
        photo_answer = {"photos_data": [
            {"photo_url": "https://streetviewpixels-pa.googleapis.com/v1/x", "photo_date": "10/01/2026 10:00:00"},
            {"photo_url": "https://example.com/p.jpg", "photo_date": "8/27/2023 19:00:00"},
        ]}
        record = build_activity(FULL, None, photo_answer, Path("p"), lambda url, path: None, NOW)
        self.assertEqual(record["latest_photo_date"], "2023-08-27")
        self.assertEqual([p["street_view"] for p in record["photos"]], [True, False])

    def test_missing_answers_and_failed_downloads(self) -> None:
        def download(url: str, path: Path) -> None:
            raise OSError("offline")

        photo_answer = {"photos_data": [{"photo_url": "https://example.com/p.jpg"}]}
        record = build_activity({"google_id": "x:y", "name": "Z"}, None, photo_answer, Path("p"), download, NOW)
        self.assertIsNone(record["last_review_date"])
        self.assertIsNone(record["owner_answered_last_review"])
        self.assertIsNone(record["photos"][0]["file"])
        self.assertEqual(record["activity_score"], 0)


if __name__ == "__main__":
    unittest.main()
