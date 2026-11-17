"""Fetches the businesses Google Maps knows about in Alameda del Río, through the Outscraper API.

Google Maps has no "everything in this neighbourhood" search, so this runs one search per
category (plus a few generic ones), biased to the neighbourhood's coordinates, then:
- deduplicates places by Google id (the same shop shows up under several categories),
- keeps a place when it falls inside the neighbourhood's bounding box or its address names it,
- writes the rest to a separate file so nothing is silently dropped.

Every call costs Outscraper credits, so the raw response is saved first and `--reuse-raw`
re-runs the filtering without calling the API again. Use `--dry-run` to see the queries.

API key: the `OUTSCRAPER_API_KEY` environment variable. See README.md and SPEC.md next to this file.
Independent of `business_pipeline`: neither package imports the other.
"""

import argparse
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

API_URL = "https://api.app.outscraper.com"
AREA_NAME = "Alameda del Río"
CITY = "Barranquilla"

# Outline of the neighbourhood, from the OpenStreetMap features named "Alameda del Río"
# (parks, nature reserve, bridge, sales office) plus a margin: south, west, north, east.
DEFAULT_BBOX = (10.984, -74.860, 11.004, -74.835)
# Search bias: the centre of the box at street-level zoom.
DEFAULT_COORDINATES = "@10.994,-74.848,16z"

# Spanish category words as Colombians search them on Google Maps. Overlap is intended:
# duplicates are removed afterwards, and a wider net finds more small businesses.
CATEGORIES = [
    "negocios", "tiendas", "comercio", "locales comerciales",
    "tienda de barrio", "minimercado", "supermercado", "panadería", "carnicería", "fruver",
    "licorera", "droguería", "farmacia",
    "restaurante", "comida rápida", "asadero", "pizzería", "hamburguesas", "cafetería",
    "heladería", "postres", "repostería", "comida a domicilio", "bar",
    "peluquería", "barbería", "salón de belleza", "uñas", "spa", "estética",
    "gimnasio", "consultorio médico", "odontólogo", "laboratorio clínico", "óptica",
    "fisioterapia", "psicólogo",
    "veterinaria", "tienda de mascotas", "peluquería canina",
    "ferretería", "cerrajería", "lavandería", "lavadero de carros", "taller mecánico",
    "papelería", "miscelánea", "tienda de ropa", "zapatería", "tienda de celulares",
    "inmobiliaria", "corresponsal bancario", "cajero automático",
    "colegio", "jardín infantil", "academia", "guardería",
    "iglesia", "centro comercial", "parqueadero", "estación de servicio", "hotel",
]


@dataclass
class Settings:
    queries: list[str]
    limit: int  # places per query
    coordinates: str
    language: str = "es"
    region: str = "CO"
    batch_size: int = 25  # queries per API request
    poll_seconds: float = 5.0
    timeout_seconds: float = 1800.0


@dataclass
class Place:
    """The fields the directory needs, plus where the place came from."""

    google_id: str
    name: str
    category: str | None
    subtypes: str | None
    phone: str | None
    website: str | None
    full_address: str | None
    borough: str | None
    latitude: float | None
    longitude: float | None
    rating: float | None
    reviews: int | None
    business_status: str | None
    working_hours: dict | None
    location_link: str | None
    in_area_reason: str | None = None  # bbox | address | None (outside)
    queries: list[str] = field(default_factory=list)


def build_queries(categories: list[str]) -> list[str]:
    return [f"{category}, {AREA_NAME}, {CITY}, Colombia" for category in categories]


# --- API -------------------------------------------------------------------------------------

Fetch = Callable[[str, dict[str, str]], dict]


def http_get_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Outscraper answered HTTP {error.code}: {body}") from error


def search_batch(queries: list[str], settings: Settings, api_key: str, fetch: Fetch = http_get_json,
                 sleep: Callable[[float], None] = time.sleep) -> list[list[dict]]:
    """One async request for several queries; returns one list of places per query."""
    params = [("query", q) for q in queries] + [
        ("limit", str(settings.limit)),
        ("language", settings.language),
        ("region", settings.region),
        ("coordinates", settings.coordinates),
        ("dropDuplicates", "true"),
        ("async", "true"),
    ]
    headers = {"X-API-KEY": api_key, "Accept": "application/json"}
    answer = fetch(f"{API_URL}/maps/search-v3?{urllib.parse.urlencode(params)}", headers)

    status_url = answer.get("results_location") or f"{API_URL}/requests/{answer['id']}"
    waited = 0.0
    while answer.get("status") not in ("Success", "Finished"):
        if answer.get("status") not in (None, "Pending"):
            raise RuntimeError(f"Outscraper request {answer.get('id')} ended as {answer.get('status')}")
        if waited >= settings.timeout_seconds:
            raise TimeoutError(f"Outscraper request still pending after {waited:.0f}s: {status_url}")
        sleep(settings.poll_seconds)
        waited += settings.poll_seconds
        answer = fetch(status_url, headers)

    data = answer.get("data") or []
    # A single query sometimes comes back as a flat list of places instead of a list of lists.
    if data and isinstance(data[0], dict):
        data = [data]
    return data


def search_all(settings: Settings, api_key: str, fetch: Fetch = http_get_json,
               sleep: Callable[[float], None] = time.sleep) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    for start in range(0, len(settings.queries), settings.batch_size):
        batch = settings.queries[start:start + settings.batch_size]
        print(f"Querying {start + 1}-{start + len(batch)} of {len(settings.queries)}...", file=sys.stderr)
        for query, places in zip(batch, search_batch(batch, settings, api_key, fetch, sleep)):
            results[query] = places
    return results


# --- Filtering -------------------------------------------------------------------------------

def plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def in_area_reason(raw: dict, bbox: tuple[float, float, float, float]) -> str | None:
    south, west, north, east = bbox
    lat, lng = raw.get("latitude"), raw.get("longitude")
    if lat is not None and lng is not None and south <= lat <= north and west <= lng <= east:
        return "bbox"
    address = " ".join(str(raw.get(key) or "") for key in ("full_address", "borough", "street"))
    if plain(AREA_NAME) in plain(address):
        return "address"
    return None


def to_place(raw: dict) -> Place:
    return Place(
        google_id=raw.get("google_id") or raw.get("place_id") or raw.get("cid") or raw.get("name", ""),
        name=raw.get("name", ""),
        category=raw.get("category") or raw.get("type"),
        subtypes=raw.get("subtypes"),
        phone=raw.get("phone"),
        website=raw.get("site"),
        full_address=raw.get("full_address"),
        borough=raw.get("borough"),
        latitude=raw.get("latitude"),
        longitude=raw.get("longitude"),
        rating=raw.get("rating"),
        reviews=raw.get("reviews"),
        business_status=raw.get("business_status"),
        working_hours=raw.get("working_hours"),
        location_link=raw.get("location_link"),
    )


def merge(results: dict[str, list[dict]], bbox: tuple[float, float, float, float]) -> list[Place]:
    """Deduplicates across queries, remembering every query that found each place."""
    places: dict[str, Place] = {}
    for query, raws in results.items():
        for raw in raws:
            if not raw or not raw.get("name"):
                continue
            place = places.get(to_place(raw).google_id)
            if place is None:
                place = to_place(raw)
                place.in_area_reason = in_area_reason(raw, bbox)
                places[place.google_id] = place
            if query not in place.queries:
                place.queries.append(query)
    return sorted(places.values(), key=lambda p: (p.category or "", p.name))


# --- Output ----------------------------------------------------------------------------------

def write_jsonl(places: list[Place], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for place in places:
            handle.write(json.dumps(asdict(place), ensure_ascii=False) + "\n")


def write_report(results: dict[str, list[dict]], inside: list[Place], outside: list[Place], path: Path) -> None:
    by_category: dict[str, int] = {}
    for place in inside:
        by_category[place.category or "(sin categoría)"] = by_category.get(place.category or "(sin categoría)", 0) + 1
    closed = [p for p in inside if p.business_status and p.business_status != "OPERATIONAL"]
    no_phone = [p for p in inside if not p.phone]

    lines = [f"# Outscraper report: {AREA_NAME}", ""]
    lines += [f"Queries: {len(results)}", f"Places returned (with repeats): {sum(len(r) for r in results.values())}"]
    lines += [f"Unique places: {len(inside) + len(outside)}", f"Inside the area: {len(inside)}",
              f"Outside the area: {len(outside)}", f"Inside without phone: {len(no_phone)}",
              f"Inside and not operational: {len(closed)}", ""]
    lines += ["## Inside the area by category"]
    lines += [f"- {cat}: {n}" for cat, n in sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))] or ["- (none)"]
    lines += ["", "## Queries that found nothing"]
    lines += [f"- {q}" for q, r in results.items() if not r] or ["- (none)"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    south, west, north, east = (float(part) for part in text.split(","))
    return south, west, north, east


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Fetch the businesses in {AREA_NAME} from Outscraper.")
    parser.add_argument("--out", type=Path, default=Path("output/outscraper"),
                        help="Output folder (default: output/outscraper)")
    parser.add_argument("--limit", type=int, default=100, help="Places per query (default: 100)")
    parser.add_argument("--categories", help="Comma-separated categories, replacing the built-in list")
    parser.add_argument("--coordinates", default=DEFAULT_COORDINATES, help=f"Search bias (default: {DEFAULT_COORDINATES})")
    parser.add_argument("--bbox", type=parse_bbox, default=DEFAULT_BBOX,
                        help="Area as south,west,north,east (default: %(default)s)")
    parser.add_argument("--reuse-raw", action="store_true", help="Filter the saved raw response, no API call")
    parser.add_argument("--dry-run", action="store_true", help="Print the queries and the worst-case cost, then exit")
    args = parser.parse_args()

    categories = [c.strip() for c in args.categories.split(",")] if args.categories else CATEGORIES
    settings = Settings(queries=build_queries(categories), limit=args.limit, coordinates=args.coordinates)
    raw_path = args.out / "outscraper_raw.json"

    if args.dry_run:
        print("\n".join(settings.queries))
        print(f"\n{len(settings.queries)} queries x up to {settings.limit} places = "
              f"at most {len(settings.queries) * settings.limit} billed places.")
        return

    if args.reuse_raw:
        results = json.loads(raw_path.read_text(encoding="utf-8"))
    else:
        api_key = os.environ.get("OUTSCRAPER_API_KEY")
        if not api_key:
            sys.exit("Set OUTSCRAPER_API_KEY (https://app.outscraper.com/profile).")
        results = search_all(settings, api_key)
        args.out.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    places = merge(results, args.bbox)
    inside = [p for p in places if p.in_area_reason]
    outside = [p for p in places if not p.in_area_reason]
    write_jsonl(inside, args.out / "outscraper_places.jsonl")
    write_jsonl(outside, args.out / "outscraper_outside.jsonl")
    write_report(results, inside, outside, args.out / "outscraper_report.md")
    print(f"{len(inside)} places in {AREA_NAME} -> {args.out / 'outscraper_places.jsonl'}")
    print(f"{len(outside)} outside the area -> {args.out / 'outscraper_outside.jsonl'}")
    print(f"Report -> {args.out / 'outscraper_report.md'}")


if __name__ == "__main__":
    main()
