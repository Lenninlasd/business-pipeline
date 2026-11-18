# Spec: WhatsApp/TSV business pipeline

This is one of two separate pipelines in this repository. The other, the Google Maps pipeline,
is specified in [src/outscraper_extract/SPEC.md](src/outscraper_extract/SPEC.md). They share
the repository and the virtual environment, never code, input or output, and they are never
merged.

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
- The Google Maps pipeline (`src/outscraper_extract/`). Decided on 2026-11-18: it stays
  separate, with its own spec and its own handoff, and is never merged with this one.
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
8. **The schema grows by the owner's decision, never by the model's.** Facts the schema has no
   field for are captured and reported as suggestions (see "Schema discovery"). Only the owner
   promotes a fact to a field.

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
| 4 | Resolve | code | observations + decisions | `output/candidates.json`, `output/schema_suggestions.md` | designed |
| 5 | Review | human | candidates | `review.tsv` (out), `decisions.tsv` (in) | designed |
| 6 | Publish | code | reviewed businesses | `output/businesses.json` | designed |

Each step is a module in `src/business_pipeline/` that runs with
`uv run python -m business_pipeline.<step>`. It reads only the previous step's files.

Two pieces sit beside the steps:
- **Schema discovery** reports facts the schema has no field for, and is written by step 4.
- **The eval** measures step 3 against a golden set. It runs by hand, never inside a pipeline
  run. Both are described after step 6.

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
    "other_facts": [{"label": "string: snake_case, English", "value": "string: as written",
                     "evidence": "string", "personal": "boolean"}],
    "evidence": "string: short literal quotes supporting the fields",
    "confidence": "number 0..1"
  }]
}
```

Rules:

- Claude never invents a value. A field not shown in the post is `null`.
- `other_facts` holds every fact about the business that no field above covers. The rules for
  it are in "Schema discovery". Claude never adds keys to the observation itself.
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

## Schema discovery

Status: designed.

The observation schema in step 3 is a first guess made from 141 posts. Posts carry facts it
has no field for, and new data will bring more. In BUHO alone there are payment methods
("todos los medios de pago", "contra entrega") and order lead times ("encárgalo"). The pipeline
neither drops these facts nor lets the model invent fields. It captures them, counts them, and
suggests them to the owner, who decides.

### Capture (step 3)

Claude puts in `other_facts` any fact that would help a resident decide to buy from or contact
the business, and that no field covers. The prompt gives it these rules:

- **Include**, for example:
  - payment methods;
  - cash on delivery;
  - order lead time or pre-order only;
  - minimum order;
  - other social networks (TikTok, Facebook);
  - specialties or dietary options;
  - service by appointment;
  - languages;
  - warranty;
  - seasonal availability.
- **Exclude:** greetings, adjectives ("deliciosas", "preciosos"), one-day promotions ("solo por
  hoy"), and anything a field already covers.
- **`label`** is short English `snake_case`, such as `payment_methods` or `order_lead_time`. The
  prompt lists a fixed set of suggested labels, which is part of `PROMPT_VERSION`. Claude reuses
  one when it fits and makes up a new one otherwise.
- **`personal: true`** marks a fact about a private person, not the business: a home address or
  apartment number, a person's full name, an ID number, health data. These values are never
  printed in reports. Only their label and count are.

After extraction, Python maps each label through the alias column of `schema/fields.tsv`. Two
labels that mean the same thing then count as one, without calling Claude again.

### Report (step 4)

Step 4 writes `output/schema_suggestions.md`, because counting needs businesses, not posts:

1. **Suggested fields.** Every label whose status is `new`, found in at least
   `SUGGEST_MIN_BUSINESSES` (3) distinct businesses, sorted by business count. For each one:
   - the number of businesses and posts;
   - the share of all businesses;
   - up to 3 example values with their post IDs (masked when `personal`).
2. **Rare labels.** `new` labels below the threshold, one line each, so nothing is hidden.
3. **Unused fields.** Optional schema fields filled in fewer than 10 % of businesses. These are
   candidates to drop from the prompt, which shortens it.
4. **Candidates for the directory.** Promoted pipeline fields filled in at least 30 % of
   `complete` businesses that the directory API does not serve. For each one, the report says
   whether the directory spec lists it among its deliberate exclusions. The directory fields and
   the exclusions table are a snapshot written in `schema/directory_fields.md`, with its date.

### Decision (`schema/fields.tsv`)

The owner records one decision per label in `schema/fields.tsv`. That file is **committed to
this repository**: it holds labels and decisions, never values, so the history of the schema
stays in git.

| Column | Meaning |
|---|---|
| `label` | the label as reported |
| `status` | `new`, `promoted`, `extra`, `ignored` or `alias` |
| `target` | for `promoted`, the field name in the schema; for `alias`, the label it merges into |
| `decided` | date of the decision |
| `notes` | why |

What each status does:

| Status | Effect |
|---|---|
| `new` | Default for an unseen label. It appears in the report. |
| `promoted` | Becomes a real field of the observation schema (step 3) and of the business record (step 4). |
| `extra` | Kept as is, published under `extra.other_facts` in `businesses.json`, and no longer suggested. |
| `ignored` | Dropped from reports and output. |
| `alias` | Counted as `target`. |

Promoting a label is one task with four parts:
1. Add the field to the step 3 schema and to this spec.
2. Add an instruction for it to the prompt and bump `PROMPT_VERSION`.
3. Add golden cases for it (see "Evaluation").
4. Run the eval to confirm that the required fields did not get worse.

The existing cache becomes stale on purpose, so the next run re-extracts with the new field.

### Two levels of promotion

| Level | Who decides | How |
|---|---|---|
| Pipeline schema | The owner, in this repository | `schema/fields.tsv` plus the task above |
| Directory schema (`alameda-dir`) | The owner, through OpenSpec | A change in `alameda-dir` that adds the column and API field. If the field is in the directory's deliberate exclusions table (payment methods, delivery flag, Instagram, catalog link...), the change must remove it from that table |

Neither the pipeline nor an agent ever edits `alameda-dir`. The "Candidates for the directory"
section of the report is the input for that change's proposal.

## Evaluation

Status: designed. It follows the eval of the opening-hours feature in `alameda-dir`
(`backend/eval/openingHours/`, design D-16): a golden set, a pure scoring function with unit
tests, and a script run by hand that reports accuracy and usage.

### Golden set

- **Location:** `<data root>/eval/golden.jsonl`. It sits next to the raw data and not in this
  repository, because the cases are real posts with real phone numbers.
- **Shape:** one case per line, `{case_id, post_id, kinds: [...], expected}`. `expected` has
  the step 3 output shape: `post_label` plus the observations, with only the fields the post
  really shows.
- **Building it:** `business_pipeline.eval add <post_id>` copies the current extraction as a
  draft expected value, marked `"reviewed": false`. The owner corrects it by hand and sets
  `"reviewed": true`. Only reviewed cases are scored.
- **Coverage, about 25 cases:**
  - phone only in the image;
  - phone only in the text;
  - TSV row with annotations;
  - several images in one post;
  - a flyer for two businesses;
  - a question, a personal sale, and an off-topic post (no observations);
  - a post with `other_facts`, and one with a `personal` fact;
  - a prompt injection attempt (a synthetic post is allowed here);
  - one case per promoted field.
- **Where new cases come from:** step 5 lists every `set` decision that contradicts an
  observation. Each one is a real extraction error and a ready-made candidate case.

### Scoring

`score_case(expected, actual)` is a pure function, unit-tested with synthetic data. It scores
each field on its own, because an extraction can get the phone right and the category wrong:

| Field | Match rule |
|---|---|
| `post_label` | exact |
| `business_name` | equal after normalization (case, accents, punctuation, spaces) |
| `category` | equal after normalization |
| `phones` | same set after phone normalization |
| `products` | recall of expected product names, normalized |
| `other_facts` | recall of expected labels after alias mapping |
| other fields | equal after normalization; `null` expected and `null` returned counts as a hit |

### Running it

`uv run python -m business_pipeline.eval run`:
- runs step 3 on every reviewed case, with the real Claude CLI and the cache disabled;
- prints pass or fail per case and field, accuracy per field, and the number of calls;
- prints the time and the usage the CLI reports.

It never runs in the test suite or in CI.

### Rules

- **When:** before any change to `PROMPT_VERSION` or to the step 3 model.
- **Acceptance:** a change is accepted only if accuracy on the required fields (`business_name`,
  `category`, `phones`) and on `post_label` is equal to or higher than the last accepted run.
  Each accepted run's results are appended to `schema/eval_history.md`: the date, the prompt
  version, the model, and the scores, without any data.
- **No leakage:** a unit test checks that no golden case's text appears in the prompt, as in the
  opening-hours eval.

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

- The only artifact this pipeline sends across repositories is `output/businesses.json`. The
  Google Maps pipeline has its own handoff (see its spec).
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
3. **Videos and PDFs.** Skipped for now. If they turn out to matter: frames plus audio
   transcription for videos, and text extraction for PDFs.
4. **Thresholds** for schema discovery (3 businesses to suggest, 10 % for unused fields, 30 % to
   suggest to the directory) are guesses. Adjust them once there are 50 or more businesses.
5. **The 120-second window** is a guess based on BUHO. Tune it if review shows posts that should
   have been split or merged.

## Tests

`uv run python -m unittest discover -s tests`

- `tests/test_whatsapp.py`: parsing, invisible characters, attachments, omitted media, system
  and deleted messages, grouping window, stable IDs, ordering.
- `tests/test_ingest.py`: export discovery, TSV annotations and extension-less references, kind
  detection without extension, unresolved references, orphan images with folder, ignored zip,
  duplicate files, determinism.

Each new step brings its tests, written before the code, with synthetic data and a fake Claude.
The Google Maps pipeline's tests (`tests/test_outscraper_*.py`) are listed in its own spec.
Planned beyond the steps:

- **Schema discovery:**
  - alias mapping;
  - the 3-business threshold;
  - masking of `personal` values;
  - statuses from `schema/fields.tsv` changing what the report shows.
- **Eval:**
  - `score_case` per field, including normalization and `null` handling;
  - only reviewed cases are scored;
  - no golden case text appears in the prompt.
