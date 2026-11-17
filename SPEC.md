# Spec: business extraction pipeline

Status as of 2026-11-17: step 1 is implemented and tested, steps 2 to 6 are designed here and not
built yet. Update the "Status" line of each step when it changes.

## Goal

Turn the raw material collected about businesses around Alameda del Río (Barranquilla, Colombia)
into a reviewed list of businesses that the directory app can load. The raw material is
WhatsApp chat exports, loose screenshots and photos, and a hand-written TSV. The result is a
JSON file with one record per business, in the shape the directory API serves.

The directory app lives in a separate repository, `alameda-dir`, which follows Spec-Driven
Development with OpenSpec. This repository does not follow that workflow. Only its output
crosses over (see "Handoff to the directory").

## Non-goals

- Loading data into the directory database. That is a change in `alameda-dir`.
- Real-time sync with WhatsApp. Each run works on the files present in the data folder.
- Merging with the Google Maps extraction (`outscraper_extract`). See "Open questions".
- Videos and PDFs. They are inventoried in step 1 and skipped by step 3 for now.

## Constraints

1. **Privacy.** The data and every output contain personal phone numbers and names. The data
   folder lives outside any repository, and `output/` is git-ignored. Tests use invented data,
   with invalid phone numbers (prefix `570`) and `example.com` links.
2. **Raw data is never modified.** Every step reads the previous layer and writes a new one.
3. **Incremental and idempotent.** Adding files to the data folder and running again must not
   duplicate anything or lose human decisions. IDs are derived from content, not from run order.
4. **Claude through Claude Code, not the API.** AI steps call the `claude` CLI in print mode,
   which bills the owner's Claude subscription. They never use API credits (see "Calling Claude").
5. **Standard library first.** Add a package only when a step really needs it, and record why in
   `pyproject.toml`. Candidates already identified: `phonenumbers` (phone normalization),
   `rapidfuzz` (name similarity), `pillow-heif` (HEIC). Step 1 uses none.
6. **English in code and docs.** Spanish only in values that come from the data or will be shown
   to directory users (category names, business names).
7. **The human commits.** Agents may edit files but never run `git commit`, `git push` or
   `git add`. They suggest a commit message instead.

## Data folder

Default location: `../businnes-info` (the misspelling is the real folder name). Contents as of
2026-11-17:

| Source | What it is | How files are linked to context |
|---|---|---|
| `WhatsApp Chat - BUHO NEGOCIOS/` | iOS export of the "BUHO NEGOCIOS" group: `_chat.txt` (~700 lines, 31 Oct to 16 Nov 2026) and 162 media files | `_chat.txt` names each file in the message that sent it |
| `WhatsApp Chat - BUHO NEGOCIOS.zip` | Same export, zipped | Ignored |
| Root images + `negocios.tsv` | Images from the "Conjunto Esmeralda" chat, which cannot be exported (probably "advanced chat privacy") | `negocios.tsv`, written by hand, one row per business |
| `Bogota/`, `Soledad/` | Images from other cities | Folder name only; likely out of area |

What the data looks like, and why the design is the way it is:

- **The information is split between text and image, differently each time.** One daily menu
  has dishes and phones in the text and a generic picture. An empanadas flyer has a bare
  "Buen día, a la orden" as text and the product, price and WhatsApp number inside the image.
  Extraction must read both together.
- **The export identifies senders by display name**, often an emoji or a single dot, not by
  phone number. Contact numbers must come from the text, the image or the TSV.
- **Heavy repetition.** Vendors repost the same flyer daily. In BUHO, 168 image references
  are 104 unique files, and one flyer appears 11 times, including once in the Esmeralda images.
- **Not every message is a business.** Questions ("¿alguien vende sopa?"), one-off personal sales
  ("medio cuñete de pintura", an apartment for rent) and system messages are mixed in.
- **Disappearing messages.** BUHO had 7-day disappearing messages until 30 Oct, and a member
  turned on 24-hour disappearing messages on 15 Nov. Anything not exported soon is lost, so
  new exports will arrive often and overlap.

Conventions for adding data:

- Put each new WhatsApp export in its own folder, keeping the `WhatsApp Chat - <name>` prefix
  WhatsApp gives it. A dated subfolder is fine, for example `whatsapp/buho/2026-11-23/WhatsApp Chat - BUHO NEGOCIOS/`.
  Overlapping exports are expected: posts with the same ID are kept once.
- For chats that cannot be exported, add images anywhere outside export folders and describe
  them in a `.tsv` file. The TSV plays the role `_chat.txt` plays for exports. Recognised
  headers, ignoring accents and case: `Imagenes`, `Nombre`, `Compartido en`, `Ubicacion`,
  `Telefono` (or `WhatsApp`), `Categoria`, `Texto del mensaje`, `Fecha`, `Remitente`, `Notas`.
  Unknown headers are kept as annotations under their lowercased name.

## Concepts

```
SOURCE  ->  POST  ->  OBSERVATION  ->  BUSINESS
```

- **Post**: one unit of evidence. A burst of WhatsApp messages from one sender with their
  attachments, a TSV row with its images, or a loose image no TSV names.
- **Observation**: what one post says about one business (name, phone, products, hours...),
  with a pointer back to the post. A post yields zero observations (a question), one, or
  several (a flyer advertising two businesses).
- **Business**: a cluster of observations about the same business, merged into one record.

Separating these layers is what makes the pipeline incremental. New files add posts, new posts
add observations, and only the clustering in step 4 is recomputed. Human decisions point at
post IDs, which never change, so they survive every rerun.

### What counts as a business

A publishable business has all three of:

| Field | Required |
|---|---|
| `name` | yes |
| `category` | yes |
| contact phone (WhatsApp preferred) | yes |
| products or menu with prices, opening hours, delivery (offered, area, cost), location, physical store or not, Instagram, website | no, but extract everything available |

A business missing a required field is **not dropped**. It gets the status `needs_review` and
waits for a human decision or for another post that supplies the missing value.

## Steps

| # | Step | Kind | Input | Output | Status |
|---|---|---|---|---|---|
| 1 | Ingest | code | data folder | `output/posts.jsonl`, `output/ingest_report.md` | done |
| 2 | Classify | code + Claude | posts | `output/classified.jsonl` | designed |
| 3 | Extract | Claude | business posts | `output/observations.jsonl`, `cache/extract/` | designed |
| 4 | Resolve | code | observations + decisions | `output/candidates.json` | designed |
| 5 | Review | human | candidates | `review.tsv` (out), `decisions.tsv` (in) | designed |
| 6 | Publish | code | reviewed businesses | `output/businesses.json` | designed |

Each step is a module in `src/business_pipeline/` that runs with
`uv run python -m business_pipeline.<step>`. It reads only the previous step's files.

### Step 1: Ingest

Status: done (`ingest.py`, `whatsapp.py`, `files.py`, `models.py`).

Discovery:

- A folder holding `_chat.txt` is a WhatsApp export. Its channel is the folder name without the
  `WhatsApp Chat - ` prefix.
- `.zip` files are listed as ignored. `.DS_Store` and `.tsv` files are never media.
- Every `.tsv` outside an export folder is read as annotations.
- Any other file outside an export folder that no TSV names becomes an `orphan_image` post. Its
  channel is its folder (`(raíz)` for the root), which keeps `Bogota` and `Soledad` visible.

Parsing `_chat.txt` (iOS, Spanish locale):

- A message starts with `[d/m/yy, H:MM:SS] sender: `, optionally preceded by U+200E. Any other
  line continues the previous message. Dates are day-first, and two-digit years mean 20yy.
- Invisible characters are removed: U+200E, U+200F, U+202A, U+202C, U+2066 to U+2069. U+202F and
  U+00A0 become spaces. A leading `~` is removed from senders.
- `<adjunto: FILE>` marks an attachment. `<Se editó este mensaje.>` is removed.
- `<kind> omitido`, optionally preceded by a file name, is media the export left out. It is
  recorded in `omitted`.
- A message is a system message if its text is `Se eliminó este mensaje.`, or if its body
  starts with U+200E and has no attachment and nothing omitted. Examples: "X añadió a Y",
  "Se activaron los mensajes temporales". System messages are counted and dropped. This rule
  relies on an export detail, and the tests pin it down.

Grouping into posts: consecutive non-system messages from the same sender, at most
`POST_WINDOW_SECONDS` (120) apart, form one post. Text is joined with newlines, and
attachments and omitted items are concatenated.

TSV rows: one post per row. Image references are split on commas and matched by exact file name
first, then by name without extension, ignoring case. References that match nothing are reported
and kept as `missing` attachments.

Attachments: path relative to the data root, SHA-256 of the content, and kind detected from
magic bytes (`image`, `video`, `pdf`, `contact`, `other`, `missing`). Extensions are not trusted:
some files have none, and HEIC is an image.

Post IDs (stable across runs and overlapping exports):

| Source | ID |
|---|---|
| WhatsApp | `whatsapp:<channel>:<ISO timestamp of first message>:<sender>` |
| TSV row | `tsv:<tsv path>:<first image stem, or name>` |
| Orphan image | `image:<first 16 hex chars of SHA-256>` |

`posts.jsonl` holds one `Post` per line (fields in `models.py`). `ingest_report.md` counts posts
by source and attachments by kind. It also lists system messages, omitted media, unresolved TSV
references, identical files found at several paths, and ignored files.

Known gap: when two exports overlap, the same post ID can appear twice in `posts.jsonl`.
Deduplication by `post_id`, keeping the first, still has to be added to `ingest()`, with a test.

First run, 2026-11-17: 141 posts (121 WhatsApp, 6 TSV, 14 orphan images), 168 image
references (104 unique), 11 system messages, 11 omitted media files, 0 unresolved references.

### Step 2: Classify

Status: designed.

Labels each post as one of these:

| Label | Example | Goes to step 3 |
|---|---|---|
| `business_offer` | a flyer, a daily menu, "Saliendo pedidos" with a photo | yes |
| `question` | "¿Alguien vende sopa?", "¿alguien tiene un electricista?" | no |
| `personal_sale` | leftover paint, an apartment for rent | no |
| `other` | greetings, chatter | no |

Rules:

- TSV posts and orphan images are always `business_offer`. A human chose to keep them.
- Posts with attachments go to step 3 directly. Step 3 also returns a label, because judging a
  flyer needs the image, and reading the image twice would cost twice.
- Text-only posts are classified by Claude with a small model (`--model haiku`), in batches of
  about 20 posts per call to save usage.
- A human can override any label in `decisions.tsv` (`include` or `exclude`).

Output: one line per post, `{post_id, label, reason}`.

### Step 3: Extract

Status: designed.

For each `business_offer` post, Claude reads the text, the TSV annotations and the image
attachments, and returns observations. One call per post.

Cache: the key is the SHA-256 of `PROMPT_VERSION`, the post text, the annotations and the
sorted attachment hashes. The result is stored in `cache/extract/<key>.json`.
- A post repeated word for word with the same images, as daily reposts are, costs one call.
- Changing the prompt means bumping `PROMPT_VERSION`, which invalidates the whole cache on purpose.
- An interrupted run resumes where it stopped.

Observation schema (also passed to `--json-schema`):

```json
{
  "post_label": "business_offer | question | personal_sale | other",
  "observations": [{
    "business_name": "string | null",
    "category": "string | null",
    "offering": "string: what is sold, in a few words",
    "phones": [{"raw": "string as written", "source": "text | image | annotation", "is_whatsapp": "boolean | null"}],
    "instagram": "string | null",
    "website": "string | null",
    "location": "string | null",
    "has_physical_store": "boolean | null",
    "delivery": {"offered": "boolean | null", "area": "string | null", "cost": "string | null"},
    "opening_hours_text": "string | null: as written",
    "products": [{"name": "string", "price_cop": "integer | null", "section": "string | null"}],
    "evidence": "string: short literal quotes supporting the fields",
    "confidence": "number 0..1"
  }]
}
```

Rules:

- Claude never invents a value. A field not shown in the post is `null`.
- `category` uses one of the directory's supported categories when one fits: Alimentos,
  Belleza, Cafetería, Comida, Deportes, Educación, Farmacia, Ferretería, Floristería,
  Lavandería, Mascotas, Mercado, Panadería, Postres, Tecnología. Otherwise it uses a short
  Spanish noun of its own.
- Python, not Claude, does deterministic post-processing:
  - phones are normalized to digits with the country code (`57` plus 10 digits for Colombian
    mobiles, which start with 3);
  - `opening_hours_text` is parsed into `{dayOfWeek, opens, closes}` ranges when the format
    allows, and left as text otherwise.
- `observations.jsonl` holds one observation per line, with `post_id`, `observation_index` and
  the cache key added.

### Step 4: Resolve

Status: designed.

Clusters observations into businesses with union-find. These links merge automatically:

1. Same normalized phone.
2. Same image SHA-256 in two posts. This links the same flyer across chats, for example
   Esmeralda and BUHO.
3. Same sender in the same WhatsApp channel. The exception is a sender whose observations have
   clearly different offerings, which is flagged instead, for example one sender in BUHO who
   sells door seals, mattresses and Christmas lights.
4. `merge` decisions from `decisions.tsv`.

Similar names (normalized: lowercase, no accents, no punctuation) only produce a **suggestion**
for review. They never merge on their own.

Merging fields:
- A human value from the TSV or `decisions.tsv` beats a value from text, which beats a value
  from an image.
- Among equal sources, the most frequent value wins, and ties go to the most recent.
- When an observation disagrees with the chosen value, the disagreement is recorded as a
  conflict for review.
- Products are a union keyed by normalized name, keeping the most recent price.

Status of each business:
- `complete`: name, category and phone present, and no open conflict.
- `needs_review`: a required field is missing, or there is a conflict or a suggestion.
- `excluded`: by decision.

Business ID: `biz:` plus the smallest phone in the cluster, or the smallest post ID when there
is no phone. Decisions never reference business IDs, only post IDs, because cluster IDs can
change as data arrives.

Output: `candidates.json`, a list of businesses. Each has its merged fields, `status`,
`missing_fields`, `conflicts`, `suggestions` and `post_ids`.

### Step 5: Review

Status: designed.

Step 4 also writes `review.tsv`, one row per business that is not `complete`: what it has,
what is missing, conflicts, suggestions, and post IDs with one attachment path to open.

The human writes decisions in `<data root>/decisions.tsv`. That file sits next to the raw data
and not in this repository, because decisions contain names and phones:

| Verb | Arguments | Effect |
|---|---|---|
| `merge` | `post_id_a`, `post_id_b` | Same business |
| `split` | `post_id` | Detach this post into its own business |
| `set` | `post_id`, `field=value` | Set a field on the business that contains the post |
| `exclude` | `post_id`, `reason` | Not a business, or out of area |
| `include` | `post_id` | Override step 2's label |

Decisions are applied on every run in file order. A decision whose post ID no longer exists
is reported, not silently ignored.

### Step 6: Publish

Status: designed.

Writes `output/businesses.json`, with one record per `complete` business, in the shape of the
directory API (`alameda-dir/openspec/specs/directory/spec.md`):

| Field | Rule |
|---|---|
| `name` | merged name |
| `category` | merged category |
| `locationSummary` | merged location, or `"Alameda del Río"` when unknown and in area |
| `hasPhysicalStore` | merged value, `false` when unknown |
| `whatsappNumber` | digits with country code, for example `573001234567` (spec "WhatsApp number validity") |
| `websiteUrl` | website or Instagram URL, or `null` |
| `openingHours` | `[{dayOfWeek 1=Mon..7=Sun, opens "HH:MM", closes "HH:MM"}]`, `[]` when unknown (spec "Opening hours validity") |

Fields the directory does not have yet go under `extra`: `products`, `delivery`, `instagram`,
`sources` (post IDs) and `numberAuthorized: false`.

## Calling Claude

AI steps run the Claude Code CLI as a subprocess:

```sh
claude -p "<prompt with the post text and the attachment paths>" \
  --allowedTools Read \
  --json-schema '<observation schema>' \
  --output-format json \
  --model <alias>
```

- **Billing.** The subprocess runs with `ANTHROPIC_API_KEY` removed from its environment, so
  the CLI uses the subscription login and never API credits. The subscription has usage
  limits per time window. The cache in step 3 lets an interrupted run continue later.
- **Least privilege.** Only the `Read` tool is allowed, so Claude can open the images and
  nothing else. Chat text is third-party content and may contain instructions. With `Read`
  only, the worst outcome is a wrong extraction, which review catches.
- **Validation.** The CLI's JSON output is parsed, and the structured result is checked against
  the schema in Python. A failed call is logged and retried once. A post that still fails is
  reported and skipped, never written half-done.
- **Model.** A small model for step 2, a capable vision model for step 3.
- **Pilot first.** Before a full run of step 3, run it on 3 or 4 known posts: the empanadas
  flyer (phone in the image), a daily menu (phones in the text), and the Kuki Club TSV row
  (annotation plus photos). Then review the quality by hand.
- **Testability.** The function that calls `claude` is injected, so tests use a fake and never
  call Claude.

## Handoff to the directory

- The only artifact that crosses repositories is `output/businesses.json`.
- The directory spec says business data is loaded "through reviewed data migrations"
  (deliberate exclusions table). Loading this JSON therefore needs an OpenSpec change in
  `alameda-dir`, for example `seed-businesses-from-import`. That change would turn the JSON
  into a versioned SQL data migration and define how a second import behaves.
- **Consent.** A number found in a group chat is not consent to be listed. In `alameda-dir`,
  the change `add-number-authorization` proposes that unauthorized WhatsApp numbers are visible
  only to the admin. As of 2026-11-17 it is on the `add-proposals` branch, not merged. Every
  number from this pipeline is published with `numberAuthorized: false`.
- Everything under `extra` is outside the directory today. The directory spec lists Instagram,
  catalog link and home delivery flag among its deliberate exclusions, and products or menus
  appear nowhere in its API. Showing any of them in the app needs its own change, one that
  removes the item from that exclusions table.

## Open questions

1. **Catalog resellers and multi-product vendors.** Is someone reselling Carmel or Pacifika
   catalogs, or someone selling unrelated gadgets, a directory business? Today step 4 flags
   them and the human decides.
2. **Area.** Are posts in BUHO always from Alameda del Río? `Bogota/` and `Soledad/` are
   expected to be excluded.
3. **Google Maps.** `outscraper_extract` finds listed businesses, and this pipeline finds
   informal ones. If they are combined, a separate step would read both outputs and match by
   phone, as `src/outscraper_extract/SPEC.md` already states.
4. **Videos and PDFs.** Skipped for now. If they turn out to matter: frames plus audio
   transcription for videos, and text extraction for PDFs.
5. **The 120-second window** is a guess based on BUHO. Tune it if review shows posts that should
   have been split or merged.

## Tests

`uv run python -m unittest discover -s tests`

- `tests/test_whatsapp.py`: parsing, invisible characters, attachments, omitted media, system
  and deleted messages, grouping window, stable IDs, ordering.
- `tests/test_ingest.py`: export discovery, TSV annotations and extension-less references, kind
  detection without extension, unresolved references, orphan images with folder, ignored zip,
  duplicate files, determinism.
- `tests/test_outscraper_extract.py`: see `src/outscraper_extract/SPEC.md`.

Each new step brings its tests, written before the code, with synthetic data and a fake Claude.
