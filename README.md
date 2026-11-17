# business-pipeline

Extracts and structures business information for the Alameda del Río directory from WhatsApp
exports, loose images, and hand-written TSV files. It lives outside the `alameda-dir` repository
because the raw data and the output contain personal phone numbers.

## Pipeline

| Step | What it does | Status |
|---|---|---|
| 1. Ingest | Turns every source into posts (`output/posts.jsonl`) | done |
| 2. Classify | Business offer, question, personal sale, or other | pending |
| 3. Extract | Claude reads text + images and returns observations | pending |
| 4. Resolve | Groups observations into businesses | pending |
| 5. Review | Applies human decisions from `decisions.tsv` | pending |
| 6. Publish | Maps businesses to the directory schema | pending |

A business needs a name, a category, and a contact number. Products, opening hours, delivery,
and location are optional. Incomplete businesses go to review, they are never dropped.

## Data folder conventions

- A folder containing `_chat.txt` is a WhatsApp export (the `.zip` copy is ignored).
- A `.tsv` file describes loose images, one row per post. Recognised headers (accents and case
  ignored): `Imagenes`, `Nombre`, `Compartido en`, `Ubicacion`, `Telefono`, `Categoria`,
  `Texto del mensaje`, `Fecha`, `Remitente`, `Notas`. Image names may omit the extension.
- Any other image not named in a TSV becomes an orphan post tagged with its folder.

## Commands

```sh
uv run python -m unittest discover -s tests          # tests
uv run python -m business_pipeline.ingest ../businnes-info   # step 1
```

Output goes to `output/` (git-ignored): `posts.jsonl` and `ingest_report.md`.

## Other extractions

These share the repository and the virtual environment, but not code or output:

- [Google Maps via Outscraper](src/outscraper_extract/README.md): businesses Google Maps lists in
  Alameda del Río ([spec](src/outscraper_extract/SPEC.md)). Output goes to `output/outscraper/`.
