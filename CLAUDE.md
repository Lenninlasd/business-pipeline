# CLAUDE.md

Business extraction pipeline for the Alameda del Río directory. Read before working:

- [SPEC.md](SPEC.md): goal, data, the six steps, the Claude calling contract, open questions.
  It is the source of truth: update it in the same task when behaviour changes.
- [README.md](README.md): how to run it.
- [src/outscraper_extract/SPEC.md](src/outscraper_extract/SPEC.md): the separate Google Maps extraction.

## Rules

- **Privacy.** The data folder (`../businnes-info`) and `output/` contain personal phone numbers.
  Never copy real data into code, tests, docs or commit messages. Tests use invented data with
  invalid phones (prefix `570`) and `example.com`. Never print real phone numbers in summaries.
- **Raw data is read-only.** Never modify, move or delete files in the data folder.
- **Claude via the CLI, not the API.** AI steps call `claude -p` with only the `Read` tool, and
  without `ANTHROPIC_API_KEY` in the environment. Never add the `anthropic` SDK or require an API key.
- **Standard library first.** Ask before adding any package, say why it is needed, and record
  the reason in `pyproject.toml`.
- **Tests first** for every step, with a fake Claude. Run
  `uv run python -m unittest discover -s tests` before reporting a task as done.
- **English** in code, comments and docs. Spanish only for data values and user-facing text.
- **The human commits.** Never run `git add`, `git commit`, `git push`, `git reset` or
  `git rebase`. When a task is done, report the files changed and the tests run, and suggest a
  commit message such as `[pipeline 2] Classify text-only posts`.
- Ask before running a step that calls Claude on the whole data set. Pilot on a few posts first.
- **Suggest schema changes, never make them on your own.**
  - When data, reports or your own reading show a recurring fact the schema has no field
    for, tell the owner. Name the label, count the businesses, and give one masked example.
    Point to `output/schema_suggestions.md`.
  - Change `schema/fields.tsv` or the observation schema only after the owner decides, and
    follow the promotion task in SPEC.md ("Schema discovery").
  - Never edit `alameda-dir`. Directory fields go through an OpenSpec change there.
