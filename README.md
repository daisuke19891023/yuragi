# yuragi

Normalize ambiguous CRUD descriptions into a verified dependency graph.

The library bundles heuristics, repository/database adapters, and agent
orchestrators.  This repository also ships a command-line interface so the
pipelines can be exercised without writing Python.

## Command-line interface

Install dependencies with `uv sync` and expose the CLI via
`uv run yuragi --help`.

### Normalize descriptions

```bash
uv run yuragi normalize --input samples/crud_requests.json
```

Reads a JSON array of normalization requests and prints a `CRUDActionList`
structure.  Override service/table aliases with `--glossary glossary.json`.

### Export the graph schema

```bash
uv run yuragi schema export --out schema.json
```

Writes the JSON Schema describing graph payloads.  Use `--out -` (default) to
stream the schema to stdout.

### Run the CRUD pipeline

```bash
uv run yuragi run-crud-pipeline \
  --requests samples/crud_requests.json \
  --repo-fixture samples/repo_hits.json \
  --db-fixture samples/db_fixture.json \
  --schema public \
  --default-service BillingService \
  --out graph.json
```

This command runs the end-to-end pipeline and produces `graph.json` in a single
step.  The repository/database fixtures emulate evidence collection so the
example works out of the box.  When integrating with real infrastructure, swap
`--repo-fixture`/`--db-fixture` for `--repo-command` (e.g. ripgrep) and
`--db-engine`/`--db-dsn` or `--db-database`.

Failures are reported as JSON on stderr and exit with a non-zero status code.
