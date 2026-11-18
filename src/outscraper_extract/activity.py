"""Estimates whether each place found by `extract` is still active.

For every place in `outscraper_places.jsonl` it asks Outscraper for the newest review and the
newest photos, downloads those photos, and combines them with the signals the search already
gave (status, verified, hours, phone...) into `activity_score`, from 0 to 100.

The score is a weighted sum, not a calibrated probability: see SPEC.md ("Activity score").
Every call costs Outscraper credits, so the raw answers are saved first and `--reuse-raw`
recomputes everything without calling the API. Run it as `python -m outscraper_extract.activity`.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from outscraper_extract.extract import Fetch, http_get_json, request_async

REVIEWS_PATH = "/maps/reviews-v3"
PHOTOS_PATH = "/maps/photos-v3"
PHOTOS_PER_PLACE = 5
BATCH_SIZE = 10  # the SDK switches to async above 10 queries; we always use async

# Points per signal; they add up to 100. Recency tables are (max days old, points).
POINTS = {"operational": 20, "verified": 10, "working_hours": 5, "phone": 5,
          "owner_answer": 5, "website": 3, "has_reviews": 2}
REVIEW_RECENCY = [(90, 30), (180, 25), (365, 15), (730, 5)]
PHOTO_RECENCY = [(90, 20), (180, 15), (365, 10), (730, 3)]
TEMPORARILY_CLOSED_CAP = 20

Download = Callable[[str, Path], None]


# --- Dates -----------------------------------------------------------------------------------

def parse_date(value: object) -> datetime | None:
    """Reads a Unix timestamp, ISO text or "MM/DD/YYYY HH:MM:SS" (Outscraper's format), as UTC."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        return datetime.fromtimestamp(int(value), tz=UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
        for pattern in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def first_date(item: dict, keys: tuple[str, ...]) -> datetime | None:
    for key in keys:
        if (date := parse_date(item.get(key))) is not None:
            return date
    return None


# --- Score -----------------------------------------------------------------------------------

def recency_points(date: datetime | None, now: datetime, table: list[tuple[int, int]]) -> tuple[int, int | None]:
    """Points for how recent `date` is, and the matching day bucket (None when too old or missing)."""
    if date is None:
        return 0, None
    age = (now - date).days
    for max_days, points in table:
        if age <= max_days:
            return points, max_days
    return 0, None


def activity_score(place: dict, last_review: datetime | None, owner_answered: bool | None,
                   latest_photo: datetime | None, now: datetime) -> tuple[int, list[str]]:
    """Returns the 0-100 score and the signals that earned points."""
    status = place.get("business_status")
    if status == "CLOSED_PERMANENTLY":
        return 0, ["closed_permanently"]

    signals: list[str] = []
    score = 0
    flags = {
        "operational": status == "OPERATIONAL",
        "verified": bool(place.get("verified")),
        "working_hours": bool(place.get("working_hours")),
        "phone": bool(place.get("phone")),
        "owner_answer": bool(owner_answered),
        "website": bool(place.get("website")),
        "has_reviews": bool(place.get("reviews")),
    }
    for name, present in flags.items():
        if present:
            score += POINTS[name]
            signals.append(name)
    for label, date, table in (("review", last_review, REVIEW_RECENCY), ("photo", latest_photo, PHOTO_RECENCY)):
        points, bucket = recency_points(date, now, table)
        if points:
            score += points
            signals.append(f"{label}_{bucket}d")

    if status == "CLOSED_TEMPORARILY":
        score = min(score, TEMPORARILY_CLOSED_CAP)
        signals.append("closed_temporarily")
    return score, signals


# --- API -------------------------------------------------------------------------------------

def first_place(item: object) -> dict | None:
    """An answer item is a place dict, or a list holding it."""
    if isinstance(item, list):
        item = item[0] if item else None
    return item if isinstance(item, dict) else None


def fetch_details(path: str, google_ids: list[str], params: dict[str, str], api_key: str,
                  fetch: Fetch = http_get_json, sleep: Callable[[float], None] = time.sleep,
                  batch_size: int = BATCH_SIZE) -> dict[str, dict | None]:
    """Asks `path` about each place, by Google id; returns the answer for each id."""
    answers: dict[str, dict | None] = {}
    for start in range(0, len(google_ids), batch_size):
        batch = google_ids[start:start + batch_size]
        print(f"{path}: {start + 1}-{start + len(batch)} of {len(google_ids)}...", file=sys.stderr)
        query = [("query", gid) for gid in batch] + [("limit", "1")] + list(params.items())
        data = request_async(path, query, api_key, fetch, sleep)
        for gid, item in zip(batch, data):
            answers[gid] = first_place(item)
    return answers


def http_download(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "business-pipeline"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# --- Records ---------------------------------------------------------------------------------

def safe_name(google_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", google_id)


def build_activity(place: dict, review_answer: dict | None, photo_answer: dict | None,
                   photos_dir: Path, download: Download, now: datetime) -> dict:
    """The place's fields plus the newest review, the newest photos and the score."""
    reviews = [(first_date(r, ("review_timestamp", "review_datetime_utc")), r)
               for r in (review_answer or {}).get("reviews_data") or []]
    reviews = [(date, r) for date, r in reviews if date is not None]
    last_review_date, last_review = max(reviews, key=lambda pair: pair[0]) if reviews else (None, None)
    owner_answered = bool(last_review.get("owner_answer")) if last_review is not None else None

    photos = []
    raw_photos = (photo_answer or {}).get("photos_data") or (photo_answer or {}).get("photos") or []
    for raw in raw_photos:
        url = next((raw.get(k) for k in ("photo_url_big", "original_photo_url", "photo_url") if raw.get(k)), None)
        if url:
            photos.append({"url": url, "date": first_date(raw, ("photo_date", "photo_datetime_utc", "photo_timestamp")),
                           # A Street View capture date says nothing about the business.
                           "street_view": "streetviewpixels" in url})
    photos.sort(key=lambda p: p["date"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    photos = photos[:PHOTOS_PER_PLACE]

    for photo in photos:
        # Named by URL, so a later run never mistakes an old file for a new photo.
        name = hashlib.sha1(photo["url"].encode()).hexdigest()[:12]
        path = photos_dir / safe_name(place.get("google_id", "")) / f"{name}.jpg"
        try:
            if not path.exists():
                download(photo["url"], path)
            photo["file"] = str(path)
        except OSError as error:
            print(f"Could not download a photo of {place.get('name')}: {error}", file=sys.stderr)
            photo["file"] = None
    latest_photo = next((p["date"] for p in photos if p["date"] and not p["street_view"]), None)

    score, signals = activity_score(place, last_review_date, owner_answered, latest_photo, now)
    return {
        **place,
        "last_review_date": last_review_date.date().isoformat() if last_review_date else None,
        "owner_answered_last_review": owner_answered,
        "latest_photo_date": latest_photo.date().isoformat() if latest_photo else None,
        "photos": [{**p, "date": p["date"].date().isoformat() if p["date"] else None} for p in photos],
        "activity_score": score,
        "activity_signals": signals,
    }


# --- CLI -------------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Add the newest review, photos and an activity score to each place.")
    parser.add_argument("--places", type=Path, default=Path("output/outscraper/outscraper_places.jsonl"),
                        help="Places from the extractor (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=Path("output/outscraper"), help="Output folder (default: %(default)s)")
    parser.add_argument("--max-places", type=int, help="Only the first N places, for a pilot")
    # "latest" came back empty in the pilot; "all" works but is not sorted by date.
    parser.add_argument("--photos-tag", default="all", choices=["all", "latest", "by_owner", "menu"],
                        help="Which photos Outscraper returns (default: %(default)s)")
    parser.add_argument("--reuse-raw", action="store_true", help="Recompute from the saved raw answers, no API call")
    parser.add_argument("--dry-run", action="store_true", help="Print the worst-case billed reviews and photos, then exit")
    args = parser.parse_args()

    places = [json.loads(line) for line in args.places.read_text(encoding="utf-8").splitlines() if line.strip()]
    places = places[:args.max_places] if args.max_places else places
    # Places Google shows no reviews or photos for are not asked about: it would only cost.
    review_ids = [p["google_id"] for p in places if p.get("reviews")]
    photo_ids = [p["google_id"] for p in places if p.get("photos_count")]
    raw_path = args.out / "outscraper_activity_raw.json"

    if args.dry_run:
        print(f"{len(places)} places: at most {len(review_ids)} billed reviews "
              f"and {len(photo_ids) * PHOTOS_PER_PLACE} billed photos.")
        return

    if args.reuse_raw:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    else:
        api_key = os.environ.get("OUTSCRAPER_API_KEY")
        if not api_key:
            sys.exit("Set OUTSCRAPER_API_KEY (https://app.outscraper.com/profile).")
        raw = {
            "reviews": fetch_details(REVIEWS_PATH, review_ids, {"reviewsLimit": "1", "sort": "newest"}, api_key),
            "photos": fetch_details(PHOTOS_PATH, photo_ids, {"photosLimit": str(PHOTOS_PER_PLACE), "tag": args.photos_tag},
                                    api_key),
        }
        args.out.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")

    now = datetime.now(UTC)
    photos_dir = args.out / "photos"
    records = [build_activity(p, raw["reviews"].get(p["google_id"]), raw["photos"].get(p["google_id"]),
                              photos_dir, http_download, now) for p in places]
    out_path = args.out / "outscraper_activity.jsonl"
    with out_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    buckets = {"80-100": 0, "50-79": 0, "20-49": 0, "0-19": 0}
    for record in records:
        s = record["activity_score"]
        buckets["80-100" if s >= 80 else "50-79" if s >= 50 else "20-49" if s >= 20 else "0-19"] += 1
    print(f"{len(records)} places -> {out_path}")
    print("Activity score: " + ", ".join(f"{k}: {v}" for k, v in buckets.items()))


if __name__ == "__main__":
    main()
