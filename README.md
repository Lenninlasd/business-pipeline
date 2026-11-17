# business-pipeline

Extracts and structures business information for the Alameda del Río directory (Barranquilla,
Colombia) from WhatsApp exports, loose images and hand-written TSV files. The result is a
reviewed JSON list of businesses that the directory app (`alameda-dir`, a separate repository)
can load.

It lives outside `alameda-dir` because the raw data and every output contain personal phone
numbers, and because it does not follow that repository's Spec-Driven Development workflow.

**What it does and why: [SPEC.md](SPEC.md).** This file only covers how to run it.

## Pipeline

```
data folder ──1 Ingest──> posts ──2 Classify──> business posts ──3 Extract (Claude)──> observations
                                                                                       │
     businesses.json <──6 Publish── reviewed businesses <──5 Review (human)── 4 Resolve ┘
```

| # | Step | Command | Status |
|---|---|---|---|
| 1 | Ingest: every source becomes posts | `uv run python -m business_pipeline.ingest ../businnes-info` | done |
| 2 | Classify: business offer or not | — | designed |
| 3 | Extract: Claude reads text + images | — | designed |
| 4 | Resolve: group observations into businesses | — | designed |
| 5 | Review: apply `decisions.tsv` | — | designed |
| 6 | Publish: map to the directory schema | — | designed |

Beside the steps:
- **Schema discovery.** Facts the schema has no field for, such as payment methods or order lead
  time, are collected. Step 4 lists them in `output/schema_suggestions.md`, and you decide in
  `schema/fields.tsv` whether each one becomes a field.
- **Eval.** `business_pipeline.eval run` scores extraction against a golden set you review.
  Run it before changing the prompt or the model.

A business needs a **name**, a **category** and a **contact number**. Products, hours, delivery
and location are extracted when present. Incomplete businesses go to review, never to the bin.

## Setup

- [uv](https://docs.astral.sh/uv/). It installs Python 3.12 for this project, and there are no
  dependencies yet.
- For steps 2 and 3: the [Claude Code](https://claude.com/claude-code) CLI, logged in with a
  Claude subscription. The pipeline calls `claude -p` and removes `ANTHROPIC_API_KEY` from its
  environment, so it uses the subscription, not API credits.

## Data folder

Default: `../businnes-info`. It is never modified. To add data:

- **WhatsApp export** (Export chat → attach media): drop the `WhatsApp Chat - <name>` folder in.
  Overlapping exports are fine. Export often, because the groups use disappearing messages.
- **Chats that cannot be exported**: add the images and describe them in a `.tsv`, one row per
  business. Headers: `Imagenes` (comma-separated, extension optional), `Nombre`,
  `Compartido en`, `Ubicacion`, `Telefono`, `Categoria`, `Texto del mensaje`, `Fecha`,
  `Remitente`, `Notas`.
- **Anything else**: images no TSV names still become posts, tagged with their folder.

## Commands

```sh
uv run python -m unittest discover -s tests                 # all tests
uv run python -m business_pipeline.ingest ../businnes-info  # step 1
```

## Output

Everything goes to `output/`, which git ignores:

| File | Step | Content |
|---|---|---|
| `posts.jsonl` | 1 | One post per line: text, attachments with hash and kind, TSV annotations |
| `ingest_report.md` | 1 | Counts, unresolved TSV references, identical files, ignored files |

## Layout

```
src/business_pipeline/   the pipeline (one module per step)
src/outscraper_extract/  Google Maps extraction, independent (see below)
schema/                  schema decisions (fields.tsv), directory snapshot, eval history: no data
tests/                   unittest, synthetic data only
SPEC.md                  pipeline spec
CLAUDE.md                rules for AI agents working here
```

## Other extractions

These share the repository and the virtual environment, but not code or output:

- [Google Maps via Outscraper](src/outscraper_extract/README.md): businesses Google Maps lists in
  Alameda del Río ([spec](src/outscraper_extract/SPEC.md)). Output goes to `output/outscraper/`.
