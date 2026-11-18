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
- Each category can carry its own limit (`--categories "gimnasio:5,restaurante:60"`); the rest
  use `--limit`. `limit` is a request parameter, so queries are grouped by limit and each group
  is batched separately. Results keep the query order.
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
   - `address`, `full_address`, `borough` or `street` contains "Alameda del Río", ignoring accents and case
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
| `website` | string \| null | `website`, or `site`. Often an Instagram or Facebook URL. |
| `full_address` | string \| null | `address`, or `full_address` |
| `borough` | string \| null | `borough` (usually the neighbourhood; search-v3 does not send it) |
| `latitude`, `longitude` | number \| null | `latitude`, `longitude` |
| `rating` | number \| null | `rating` |
| `reviews` | integer \| null | `reviews` (count) |
| `business_status` | string \| null | `business_status` (`OPERATIONAL`, `CLOSED_TEMPORARILY`, …) |
| `verified` | boolean \| null | `verified` (the owner claimed the listing) |
| `photos_count` | integer \| null | `photos_count` |
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

At most, a run bills the sum of the per-query limits: 62 × 100 = 6,200 with the defaults.
Outscraper bills every returned place, including those outside the area, which are only
filtered out afterwards. The API has no parameter that restricts results to an area:
`coordinates` sets the centre, and whether its zoom narrows the search is undocumented.
`dropDuplicates` may reduce that, depending on how Outscraper bills. The guardrails:

- `--dry-run` prints the queries and that maximum without calling the API;
- `--limit`, `--categories` and per-category `:N` limits shrink a run;
- `--reuse-raw` re-runs the filtering for free.

## Known limits and open questions

- Google Maps returns at most about 120 results per search, whatever `limit` says. Categories
  with more listings than that are cut short. The fix would be narrower categories or several
  centre points, if the report shows a category hitting the cap.
- The bounding box is approximate (see "Area definition"). Places near the edges can land on
  the wrong side, so check `outscraper_outside.jsonl` and the edges of the inside list by hand.
- A business without a Google Maps listing cannot be found this way. The WhatsApp/TSV pipeline
  covers those.

## Activity (`activity.py`)

A second, separate step estimates whether each place is still active. It reads
`outscraper_places.jsonl` and, for each place, asks Outscraper by `google_id`:

| | Reviews | Photos |
|---|---|---|
| Endpoint | `GET /maps/reviews-v3` | `GET /maps/photos-v3` |
| Parameters | `reviewsLimit=1`, `sort=newest`, `limit=1`, `async=true` | `photosLimit=5`, `tag=all` (`--photos-tag`: `latest`, `by_owner`, `menu`), `limit=1`, `async=true` |
| Skipped when | the place has `reviews` = 0 | the place has `photos_count` = 0 |
| Fields read | `reviews_data[].review_timestamp` (or `review_datetime_utc`), `owner_answer` | `photos_data[]`: `photo_url_big` (or `original_photo_url`, `photo_url`), `photo_date` (`M/D/YYYY H:MM:SS`). Also sent: `photo_tags` (e.g. `front`), `photo_upload_source`. |

The endpoint and parameter names come from the official Python SDK. The response field names
are not documented anywhere we found: the code accepts the likely variants, and a pilot must
confirm them before a full run. Pilots on 3 places confirmed the review and photo fields.
With `tag=latest`, `photos_data` came back empty even for a place with 93 photos, so the
default is `tag=all`. Its photos are not sorted by date: the 5 returned are not always the 5
newest, so `latest_photo_date` can be older than the real newest photo. Street View images
(`streetviewpixels` URLs) are kept and downloaded, but their date does not count as a recent
photo. Dates are read as Unix timestamps, ISO text or
`MM/DD/YYYY HH:MM:SS`, all as UTC.

Requests carry up to 10 ids. Billing: 1 review and up to 5 photos per place asked about. Both
products have their own free tier (500 reviews and 500 photos per 30 days), then $3 per 1,000
reviews and $2 per 1,000 photos. `--dry-run` prints the worst case; `--max-places N` pilots
on the first N places.

Photos are downloaded at once, because Google's links may expire, to
`photos/<google_id>/<hash of the URL>.jpg`. A file that already exists is not downloaded
again. A failed download leaves `file` as null and the run goes on.

### Activity score

`activity_score` (0-100) adds points per signal. It is a heuristic, not a calibrated
probability.

| Signal | Points |
|---|---|
| `business_status` is `OPERATIONAL` | 20 |
| Newest review at most 90 / 180 / 365 / 730 days old | 30 / 25 / 15 / 5 |
| Newest photo at most 90 / 180 / 365 / 730 days old | 20 / 15 / 10 / 3 |
| `verified` (the owner claimed the listing) | 10 |
| Has `working_hours` | 5 |
| Has `phone` | 5 |
| The owner answered the newest review | 5 |
| Has `website` | 3 |
| Has at least one review | 2 |

Recent activity is the strongest signal, so it also sets a floor. A review from yesterday
proves the place was open yesterday, whatever its profile says; a review counts more than a
photo. The score is the larger of the sum above and the floor plus a profile bonus (0-5 points,
in proportion to the profile signals: every row except the review and photo dates).

| Newest review at most | Floor | Newest photo at most | Floor |
|---|---|---|---|
| 7 days | 95 | 30 days | 85 |
| 30 days | 90 | 90 days | 80 |
| 90 days | 85 | 180 days | 65 |
| 180 days | 70 | 365 days | 50 |
| 365 days | 55 | | |

The highest floor applies, and adds a signal such as `recent_review_7d`. Street View photos
never set a floor.

`CLOSED_PERMANENTLY` gives 0 whatever the other signals. `CLOSED_TEMPORARILY` caps the score at
20. Recency is counted from the run date.

### Output

| File | Content |
|---|---|
| `outscraper_activity_raw.json` | The answers as received: `{"reviews": {google_id: place}, "photos": {google_id: place}}`. It holds reviewer names and review text: personal data, kept only here. `--reuse-raw` recomputes from it for free. |
| `outscraper_activity.jsonl` | Every field of the place, plus `last_review_date`, `owner_answered_last_review`, `latest_photo_date`, `photos` (`url`, `date`, `street_view`, `file`), `activity_score` and `activity_signals` (the signals that earned points, e.g. `review_90d`). |
| `photos/` | The downloaded photos. They may show people's faces: never copy them out of `output/`. |

## Tests

`tests/test_outscraper_extract.py` covers dedup across queries, the bbox and address rules
(including accents), batching of queries, grouping by per-category limit, parsing of
`--categories`, the billing maximum, and async polling. `tests/test_outscraper_activity.py`
covers date parsing, the score (full, empty, old, closed, the recency floor, the profile bonus), batching by `google_id`, picking
the newest review and photos, Street View photos, and failed downloads. Both inject a fake HTTP client, so they
never call the API.
