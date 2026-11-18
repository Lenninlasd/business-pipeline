# Spec: Google Maps pipeline for Alameda del Río

This is one of two separate pipelines in this repository. The other, the WhatsApp/TSV
pipeline, is specified in the root [SPEC.md](../../SPEC.md). They share the repository and the
virtual environment, never code, input or output, and they are never merged here.

## Goal

Find every business Google Maps lists inside Alameda del Río, Barranquilla, and describe each
one with what Google Maps knows: contact data, opening hours and status, how likely it is to be
still active, and what its photos show (offering, products and prices, delivery, payments).
The result is handed to the directory repository, `alameda-dir`, which turns it into the
directory's own shape (see "Handoff").

## Steps

Each step reads the previous one's output. All output goes to `output/outscraper/`, which git
ignores.

| # | Step | Module | Reads | Writes | Status |
|---|---|---|---|---|---|
| 1 | Search | `extract.py` | Outscraper `maps/search-v3` | `outscraper_places.jsonl`, `outscraper_outside.jsonl`, `outscraper_report.md`, `outscraper_raw.json` | done |
| 2 | Activity | `activity.py` | step 1 + Outscraper reviews and photos | `outscraper_activity.jsonl`, `photos/`, `outscraper_activity_raw.json` | done |
| 3 | Photos | `photos.py` | step 2 + Claude CLI | `outscraper_photo_facts.jsonl`, `outscraper_photo_report.md`, `cache/photos/` | done |

Running step 1 again changes the set of places, so steps 2 and 3 must run again after it.

## Handoff

The pipeline's final output is two JSONL files and one image folder, all in
`output/outscraper/`:

| Artifact | Content | Spec |
|---|---|---|
| `outscraper_activity.jsonl` | One line per business inside the area: every Google Maps field, plus the newest review, 5 photos, `activity_score` (0-100) and `activity_reasons` | "Output schema" (step 1) and "Step 2: Activity" |
| `outscraper_photo_facts.jsonl` | One line per business, same `google_id`s: `status`, `offering`, `products` with prices, hours, delivery, phones, links, `other_facts`, each with its photo, date and `stale` flags | "Step 3: Photos" |
| `photos/<google_id>/<hash>.<ext>` | The images both files point to, in `photos[].file`, `photo_file` and `photo_files` | "Step 2: Activity" |

Everything else is intermediate: raw API answers, `outscraper_places.jsonl` (a subset of
`outscraper_activity.jsonl`), the outside list kept for review, the reports and the cache.

For the consumer in `alameda-dir`:

- **Join** the two files by `google_id`. Every place in `outscraper_activity.jsonl` has a line in
  `outscraper_photo_facts.jsonl`, with `status` `ok`, `no_photos` or `failed`.
- **Paths** are relative to this repository's root (`output/outscraper/photos/...`). Copy the
  `photos/` folder along with the files, or rewrite the paths.
- **Filter** with the typed fields: `activity_score`, `business_status`, `stale`,
  `price_stale`, the dates. `activity_reasons`, `offering.text` and `evidence` are text for
  people, in Spanish; do not parse them.
- **Dates.** Every run is a snapshot: recency is counted from the run date, and `as_of`,
  `last_review_date` and `photo_date` say how old each fact is.
- **Shape.** This pipeline does not produce the directory's format. Mapping fields to the
  directory API (`alameda-dir/openspec/specs/directory/spec.md`) is done in `alameda-dir`.
- **Consent.** A phone number on a Google Maps listing or in a photo is not consent to be
  listed. Treat every number as unauthorized, as the WhatsApp pipeline does.
- **Photos are source material, not assets.** They belong to whoever uploaded them to Google
  Maps, and Google's terms limit reuse. They may also show people. Publishing them in the
  directory is not cleared.

## Non-goals

- Merging with the WhatsApp/TSV pipeline. Decided on 2026-11-18: the two stay separate, and
  each hands off its own output.
- Producing the directory's format (`businesses.json`). That happens in `alameda-dir`.
- Emails and social profiles through Outscraper's contacts enrichment (`domains_service`).
- Keeping the data up to date. Each run is a snapshot.

## Constraints

- Every place, review and photo Outscraper returns is billed, repeats included. A run must never
  be repeated just to change the filtering: every step has `--reuse-raw` or a cache.
- Claude runs through the CLI with the subscription login, never API credits (root CLAUDE.md).
- Results contain phone numbers, so output stays in `output/`, which git ignores.
- Only the Python standard library, like the rest of the repository.

## Step 1: Search (`extract.py`)

### Area definition

OpenStreetMap has only a point for the neighbourhood (`node 8234797012`, 10.9949, −74.8439), not
an outline. The bounding box comes from the OSM features named "Alameda del Río": Parque
Boulevard, Reserva Natural, EBAR, the entrance parks, the sales office and Puente Alameda del Río.
They span roughly 10.987–10.998 N and 74.857–74.838 W. A margin is added on every side:

| | South | West | North | East |
|---|---|---|---|---|
| Default | 10.984 | −74.860 | 11.004 | −74.835 |

The box is a rectangle, so it also covers a little of the neighbouring areas. `--bbox` overrides it.

### Search strategy

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

### API contract

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

### Processing rules

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

### Output schema

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

### Cost

At most, a run bills the sum of the per-query limits: 62 × 100 = 6,200 with the defaults.
Outscraper bills every returned place, including those outside the area, which are only
filtered out afterwards. The API has no parameter that restricts results to an area:
`coordinates` sets the centre, and whether its zoom narrows the search is undocumented.
`dropDuplicates` may reduce that, depending on how Outscraper bills. The guardrails:

- `--dry-run` prints the queries and that maximum without calling the API;
- `--limit`, `--categories` and per-category `:N` limits shrink a run;
- `--reuse-raw` re-runs the filtering for free.

### Known limits and open questions

- Google Maps returns at most about 120 results per search, whatever `limit` says. Categories
  with more listings than that are cut short. The fix would be narrower categories or several
  centre points, if the report shows a category hitting the cap.
- The bounding box is approximate (see "Area definition"). Places near the edges can land on
  the wrong side, so check `outscraper_outside.jsonl` and the edges of the inside list by hand.
- A business without a Google Maps listing cannot be found this way. The WhatsApp/TSV pipeline
  covers those.

## Step 2: Activity (`activity.py`)

This step estimates whether each place is still active. It reads
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
`photos/<google_id>/<hash of the URL>.<ext>`. The extension follows the content (`.jpg`,
`.png`, `.gif`, `.webp`): Google serves some PNGs under photo URLs. A photo whose file already
exists is not downloaded again. A failed download leaves `file` as null and the run goes on.

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
in proportion to the profile signals: every row except the review and photo dates, rounded half
up, so 4.5 gives 5).

| Newest review at most | Floor | Newest photo at most | Floor |
|---|---|---|---|
| 7 days | 95 | 30 days | 85 |
| 30 days | 90 | 90 days | 80 |
| 90 days | 85 | 180 days | 65 |
| 180 days | 70 | 365 days | 50 |
| 365 days | 55 | | |

The highest floor applies. Street View photos
never set a floor.

`CLOSED_PERMANENTLY` gives 0 whatever the other signals. `CLOSED_TEMPORARILY` caps the score at
20. Recency is counted from the run date.

### Reasons

`activity_reasons` explains the score in Spanish, from most to least important:

1. A closure, if any: `"Cerrado permanentemente en Google Maps"` (the only reason then), or
   `"Cerrado temporalmente en Google Maps (máximo 20)"`.
2. What decided the number:
   - when the floor wins, the floor (`"Reseña hace 2 días (piso de 95)"`) and the bonus
     (`"Perfil: 45 de 50 puntos (+5)"`);
   - otherwise, every signal that added points, from most to least (`"Operativo en Google Maps
     (+20)"`, `"Última reseña hace 1 año y 6 meses (+5)"`).
3. What is missing, without points: `"Sin reseñas"`, `"Sin fotos"`, `"Sin reseñas recientes: la
   última es de hace 2 años"` (older than every floor, so over a year), and `"El dueño no
   respondió la última reseña"`. Lesser gaps, such as no website, are not listed.

Ages are rounded down: `hoy`, `hace N días`, `hace N meses` (30 days each), `hace N años y N
meses`. The text is for people. Code that filters should read `last_review_date`,
`latest_photo_date` and `owner_answered_last_review`, not parse the reasons.

### Output

| File | Content |
|---|---|
| `outscraper_activity_raw.json` | The answers as received: `{"reviews": {google_id: place}, "photos": {google_id: place}}`. It holds reviewer names and review text: personal data, kept only here. `--reuse-raw` recomputes from it for free. |
| `outscraper_activity.jsonl` | Every field of the place, plus `last_review_date`, `owner_answered_last_review`, `latest_photo_date`, `photos` (`url`, `date`, `street_view`, `file`), `activity_score` and `activity_reasons` (see "Reasons"). |
| `photos/` | The downloaded photos. They may show people's faces: never copy them out of `output/`. |

## Step 3: Photos (`photos.py`)

This step reads the downloaded photos with Claude. It reads `outscraper_activity.jsonl` and,
for each place with at least one photo that is not Street View and whose file exists, makes one
call with all those photos (up to 5). Places without such photos make no call.

### Calling Claude

It follows the pipeline's contract (root SPEC.md, "Calling Claude"), with stricter flags:

```sh
claude -p "<prompt>" --tools Read --allowedTools Read --strict-mcp-config \
  --no-session-persistence --json-schema '<SCHEMA>' --output-format json --model sonnet
```

- `ANTHROPIC_API_KEY` is removed from the environment, so the CLI uses the subscription login.
- `--tools Read` leaves Read as the only tool available, and `--strict-mcp-config` loads no MCP
  server. Text in photos is third-party content: the prompt says never to follow it.
- The answer is `structured_output` in the CLI's JSON, or else the JSON object in `result`.
  Python validates it against `SCHEMA` (a subset of JSON Schema, checked without packages) and
  checks that every cited photo number was sent. A failure is retried once, then the place is
  reported as `failed`. After 3 failures in a row the run stops, as that is most likely the
  subscription's usage limit; running it again resumes from the cache.
- Cache: `cache/photos/<key>.json`, where the key is the SHA-256 of `PROMPT_VERSION`, the
  model, the place's name and category, and the photos' content hashes in order. Failures are
  never cached. Changing the prompt means bumping `PROMPT_VERSION`.

### What Claude returns

Each fact cites the number of the photo it comes from, in the prompt's order:

| Field | Content |
|---|---|
| `photos[]` | `index`, `kind` (`menu`, `price_list`, `flyer`, `storefront`, `product`, `interior`, `other`), `has_text` |
| `products[]` | `name`, `price_cop` (integer or null), `section`, `photo`. From a menu, price list or sign, and also brands or products seen on shelves or packaging. |
| `offering` | `text`: what it sells, in a few Spanish words, also judged from photos without text; `photos`: the photo numbers it rests on (at least one); or null |
| `opening_hours` | `text` as written, `photo`; or null |
| `delivery` | `offered`, `area`, `cost`, `photo`; or null |
| `phones[]` | `raw` as written, `is_whatsapp`, `photo` |
| `instagram`, `website` | `value`, `photo`; or null |
| `has_physical_store` | true when a photo shows the premises, otherwise null |
| `other_facts[]` | `label` (snake_case, English), `value`, `evidence`, `photo`. Only what helps a customer: payment, promotions, services, parking, accessibility, a name on a sign that differs from Google's. Not decoration, furniture, mascots or mood. |
| `evidence`, `confidence` | literal quotes; 0 to 1 |

Python normalizes `other_facts` labels to snake_case and merges the synonyms Claude used for
the same fact (`LABEL_SYNONYMS`: e.g. `sign_business_name` and `business_name_on_sign` become
`sign_name`), and drops labels that do not help a customer (`slogan`). This runs on cached
answers too, so changing the map needs no Claude call.

Claude never invents a value, copies text in Spanish as written, and does not describe people.
Products and services need a written name (menu, price list, sign, label or packaging). Nothing
is inferred from the people in a photo: a child having a haircut does not mean "cortes para
niños" unless it is written.

### Dates and staleness (Python, not Claude)

`PROMPT_VERSION` 2 added `offering` and narrowed `other_facts`, after a pilot on 4 places
where 16 photos held no menu or price list and the facts included decoration. Version 3
stopped inferring services from people: v2 listed "cortes para niños" from a photo of a child
having a haircut, with nothing written.

Many photos are years old, so every fact gets its photo's `photo_file` and `photo_date`, and a
`stale` flag by kind of fact:

| Fact | Trusted while the photo is at most |
|---|---|
| Price (`price_stale`) | 365 days |
| Product or service, offering (`stale`) | 3 years |
| Hours, delivery, phones, social links, other facts (`stale`) | 2 years |

The offering takes the date of the newest photo it cites and lists all of them in
`photo_files`. An undated photo counts as stale. `price_stale` is null when there is no price. Products with
the same name, ignoring accents, case and spaces, are merged, keeping the one from the newest
photo. `as_of` is the date of the newest photo sent. When this is merged with the WhatsApp
pipeline, WhatsApp facts should win: they are recent.

### Output

| File | Content |
|---|---|
| `outscraper_photo_facts.jsonl` | One line per place: `google_id`, `name`, `category`, `status` (`ok`, `no_photos`, `failed`). When `ok`: `as_of`, `photos` (`file`, `date`, `kind`, `has_text`), the facts above with `photo_file`, `photo_date` and `stale`, `evidence`, `confidence`, `model`, `prompt_version`, `cache_key`, `cached`. When `failed`: `error`. |
| `outscraper_photo_report.md` | Places read, from the cache, without photos and failed; coverage (photos with text, offering, products, current and stale prices, hours, delivery, Instagram, physical store); photos by kind; failures. |
| `cache/photos/` | Claude's validated answers. Phones in photos make them personal data. |

`--dry-run` counts places, photos and cached answers without calling Claude. `--only` (google
ids) and `--max-places` pilot on a few places.

## Tests

`tests/test_outscraper_extract.py` covers dedup across queries, the bbox and address rules
(including accents), batching of queries, grouping by per-category limit, parsing of
`--categories`, the billing maximum, and async polling. `tests/test_outscraper_activity.py`
covers date parsing, the score (full, empty, old, closed, the recency floor, the profile bonus and its rounding, the order and text of the reasons, Spanish ages), batching by `google_id`, picking
the newest review and photos, Street View photos, file extensions, skipping files already
downloaded, and failed downloads. `tests/test_outscraper_photos.py` covers schema
validation, reading the CLI's answer, the CLI command (Read only, no API key), which photos are
sent, the prompt, dating and staleness, merging duplicate products, label normalization, the
cache, the retry and places without photos. The first two inject a fake HTTP client and the third a fake Claude, so
no test calls Outscraper or Claude.
