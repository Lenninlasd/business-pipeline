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
| `--limit N` | `100` | Places requested per query. The bill grows with the sum of the limits. |
| `--categories "a,b:N"` | built-in list (62) | Search only these categories. `:N` caps one category at N places; the others use `--limit`. |
| `--coordinates` | `@10.994,-74.848,16z` | Where Google Maps centres each search. |
| `--bbox S,W,N,E` | `10.984,-74.860,11.004,-74.835` | What counts as inside the neighbourhood. |
| `--reuse-raw` | off | Skip the API and filter `outscraper_raw.json` again. |
| `--out DIR` | `output/outscraper` | Output folder. |

Start with `--dry-run`, then a small `--limit` or a few `--categories`, before a full run.

## A free run (up to 500 places)

Outscraper's first 500 places in each 30-day period are free. After that it charges
$3 per 1,000 (see [SPEC.md](SPEC.md#cost)). A run bills at most the sum of its per-query
limits, so the plan below gives each category its own limit and adds up to 500.

`limit` is a ceiling, not a filter. Google Maps does not stop at the neighbourhood edge: a
search for a category with 2 gyms inside still returns up to its limit, the rest from nearby,
and every returned place is billed. So scarce categories get a small limit and busy ones a large
one.

1. In the Outscraper dashboard, check that no free places have been used this period. Any
   earlier usage counts against the 500.
2. Set the API key (see [Setup](#setup)).
3. Check the plan without calling the API. The last line must read
   `20 queries, at most 500 billed places.`

   ```sh
   uv run python -m outscraper_extract --dry-run --categories "restaurante:70,comida rápida:50,tienda de barrio:40,peluquería:30,salón de belleza:30,barbería:25,droguería:25,panadería:25,cafetería:25,minimercado:25,licorera:20,ferretería:20,papelería:20,odontólogo:20,consultorio médico:20,heladería:15,supermercado:10,veterinaria:10,lavandería:10,gimnasio:10"
   ```

4. Run the same command without `--dry-run`.
5. Read `output/outscraper/outscraper_report.md`. Expect fewer than 500 unique places inside
   the neighbourhood: the same shop shows up under several categories, and some results fall
   outside the area. A category whose inside count is close to its limit was probably cut
   short: give it more next time. One with almost everything outside can go lower.

The categories are the common neighbourhood businesses, and the limits are first guesses.
The generic ones ("negocios", "tiendas", ...) are left out because they mostly return places
the specific searches already find. To cover other categories for free, wait for the next 30-day period and run them with
another `--out` folder (for example `--out output/outscraper-2`). Each run overwrites the files
in its folder.

## Output

Everything goes to `output/outscraper/`. The repository ignores that folder because the
results contain phone numbers.

| File | Content |
|---|---|
| `outscraper_raw.json` | The API response as received, one list of places per query. |
| `outscraper_places.jsonl` | One unique place per line, inside the neighbourhood. |
| `outscraper_outside.jsonl` | Unique places the searches returned that fall outside it, kept for review. |
| `outscraper_report.md` | Counts by category, places without a phone or not operational, empty queries. |
