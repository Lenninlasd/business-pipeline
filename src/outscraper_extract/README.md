# outscraper_extract

Lists the businesses Google Maps knows about in Alameda del Río (Barranquilla), through the
[Outscraper](https://outscraper.com) API. It is a separate source from the WhatsApp/TSV pipeline
in [`business_pipeline`](../../README.md): it does not read or write that pipeline's files, and
nothing in the pipeline depends on it.

What it does and why is in [SPEC.md](SPEC.md).

## Setup

You need an Outscraper API key (https://app.outscraper.com/profile), set in an environment variable:

```sh
export OUTSCRAPER_API_KEY=...
```

It uses only the Python standard library, so it needs nothing beyond the project's `uv` environment.

## Commands

Run them from the repository root:

```sh
uv run python -m outscraper_extract --dry-run           # queries + worst-case billed places
uv run python -m outscraper_extract --limit 50          # call the API (costs credits)
uv run python -m outscraper_extract --reuse-raw         # re-filter the saved response, free
uv run python -m unittest tests.test_outscraper_extract # tests
```

| Option | Default | Purpose |
|---|---|---|
| `--limit N` | `100` | Places requested per query. The bill grows with queries × limit. |
| `--categories "a,b"` | built-in list (62) | Search only these categories. |
| `--coordinates` | `@10.994,-74.848,16z` | Where Google Maps centres each search. |
| `--bbox S,W,N,E` | `10.984,-74.860,11.004,-74.835` | What counts as inside the neighbourhood. |
| `--reuse-raw` | off | Skip the API and filter `outscraper_raw.json` again. |
| `--out DIR` | `output/outscraper` | Output folder. |

Start with `--dry-run`, then a small `--limit` or a few `--categories`, before a full run.

## Output

Everything goes to `output/outscraper/`. The repository ignores that folder because the
results contain phone numbers.

| File | Content |
|---|---|
| `outscraper_raw.json` | The API response as received, one list of places per query. |
| `outscraper_places.jsonl` | One unique place per line, inside the neighbourhood. |
| `outscraper_outside.jsonl` | Unique places the searches returned that fall outside it, kept for review. |
| `outscraper_report.md` | Counts by category, places without a phone or not operational, empty queries. |
