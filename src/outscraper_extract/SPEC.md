# Spec: Google Maps extraction for Alameda del Río

## Goal

Produce a list of every business Google Maps lists inside Alameda del Río, Barranquilla, with
enough data to contact it: name, category, phone, address, coordinates, opening hours and status.

It complements the WhatsApp/TSV pipeline (`business_pipeline`). That pipeline finds informal
businesses that only advertise in neighbourhood chats. This extraction finds businesses that
have a Google Maps listing. The two sources overlap only in part.

## Non-goals

- Merging with the pipeline's posts or businesses. If that is ever needed, it will be a separate
  step that reads both outputs. Neither package imports the other.
- Reviews, photos, emails or social profiles. Outscraper charges extra for these enrichments.
- Keeping the data up to date. Each run is a snapshot.

## Constraints

- Every place Outscraper returns is billed, repeats included. A run must never be repeated just
  to change the filtering.
- Results contain phone numbers, so output stays in `output/`, which git ignores.
- Only the Python standard library, like the rest of the repository.

## Area definition

OpenStreetMap has only a point for the neighbourhood (`node 8234797012`, 10.9949, −74.8439), not
an outline. The bounding box comes from the OSM features named "Alameda del Río": Parque
Boulevard, Reserva Natural, EBAR, the entrance parks, the sales office and Puente Alameda del Río.
They span roughly 10.987–10.998 N and 74.857–74.838 W. A margin is added on every side:

| | South | West | North | East |
|---|---|---|---|---|
| Default | 10.984 | −74.860 | 11.004 | −74.835 |

The box is a rectangle, so it also covers a little of the neighbouring areas. `--bbox` overrides it.

## Search strategy

Google Maps has no "everything in this area" search. The script therefore makes one search per
category:

- The query is `"<category>, Alameda del Río, Barranquilla, Colombia"`.
- There are 62 categories in Spanish (`CATEGORIES` in `extract.py`), including generic ones
  ("negocios", "tiendas", "comercio"). They overlap on purpose: a wider net finds more small
  businesses, and duplicates are removed afterwards.
- Every search is centred on `@10.994,-74.848,16z` (street-level zoom), with `language=es` and
  `region=CO`.

## API contract

| | |
|---|---|
| Endpoint | `GET https://api.app.outscraper.com/maps/search-v3` |
| Auth | `X-API-KEY` header, read from `OUTSCRAPER_API_KEY` |
| Parameters | `query` (repeated, up to 25 per request), `limit`, `language`, `region`, `coordinates`, `dropDuplicates=true`, `async=true` |
| Async flow | The response carries `id`, `status` and `results_location`. The script polls `results_location` (or `/requests/{id}`) every 5 s until `status` is `Success`, for at most 30 minutes. |
| Result | `data`: one list of places per query, in query order. If `data` is a flat list of places, it is treated as the result of a single query. |

The parameter names come from Outscraper's official Python SDK, not from its API reference. The
first real run confirms them. If Outscraper rejects one, it answers with an HTTP error, and the
script stops and shows the first 500 characters of the response.

## Processing rules

1. **Raw first.** The full response is saved to `outscraper_raw.json` before any processing, as
   `{query: [place, ...]}`.
2. **Identity.** A place's key is the first non-empty value of `google_id`, `place_id`, `cid` or
   `name`. Entries without a `name` are skipped.
3. **Dedup.** The first occurrence of a place supplies its fields. Later occurrences only add
   their query to `queries`.
4. **Inside or outside.** A place is inside when:
   - its coordinates fall within the bounding box (`in_area_reason = "bbox"`), or
   - `full_address`, `borough` or `street` contains "Alameda del Río", ignoring accents and case
     (`in_area_reason = "address"`).

   Any other place is outside (`in_area_reason = null`). Outside places are written to their own
   file and never dropped.
5. **Status.** Places that are closed for good or for now stay in the list. The report counts
   them, and `business_status` shows which they are.
6. **Order.** Places are sorted by category, then by name.

## Output schema

`outscraper_places.jsonl` and `outscraper_outside.jsonl` hold one JSON object per line:

| Field | Type | Source in the Outscraper response |
|---|---|---|
| `google_id` | string | `google_id` / `place_id` / `cid` |
| `name` | string | `name` |
| `category` | string \| null | `category`, or `type` |
| `subtypes` | string \| null | `subtypes` (comma-separated) |
| `phone` | string \| null | `phone` |
| `website` | string \| null | `site` |
| `full_address` | string \| null | `full_address` |
| `borough` | string \| null | `borough` (usually the neighbourhood) |
| `latitude`, `longitude` | number \| null | `latitude`, `longitude` |
| `rating` | number \| null | `rating` |
| `reviews` | integer \| null | `reviews` (count) |
| `business_status` | string \| null | `business_status` (`OPERATIONAL`, `CLOSED_TEMPORARILY`, …) |
| `working_hours` | object \| null | `working_hours` |
| `location_link` | string \| null | `location_link` (Google Maps URL) |
| `in_area_reason` | `"bbox"` \| `"address"` \| null | computed (rule 4) |
| `queries` | string[] | every query that returned the place |

`outscraper_report.md` lists:

- the number of queries;
- places returned, counting repeats;
- unique places, inside and outside the area;
- inside places with no phone, and inside places that are not operational;
- inside places counted by category;
- queries that returned nothing.

## Cost

At most, a run bills `queries × limit` places: 62 × 100 = 6,200 with the defaults.
`dropDuplicates` may reduce that, depending on how Outscraper bills. The guardrails:

- `--dry-run` prints the queries and that maximum without calling the API;
- `--limit` and `--categories` shrink a run;
- `--reuse-raw` re-runs the filtering for free.

## Known limits and open questions

- Google Maps returns at most about 120 results per search, whatever `limit` says. Categories
  with more listings than that are cut short. The fix would be narrower categories or several
  centre points, if the report shows a category hitting the cap.
- The bounding box is approximate (see "Area definition"). Places near the edges can land on
  the wrong side, so check `outscraper_outside.jsonl` and the edges of the inside list by hand.
- A business without a Google Maps listing cannot be found this way. The WhatsApp/TSV pipeline
  covers those.

## Tests

`tests/test_outscraper_extract.py` covers dedup across queries, the bbox and address rules
(including accents), batching of queries, and async polling. It uses an injected fake HTTP
client, so it never calls the API.
