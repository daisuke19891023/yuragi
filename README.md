# yuragi

Normalize ambiguous CRUD descriptions into a verified dependency graph.

The library bundles heuristics, repository/database adapters, and agent
orchestrators.  It exposes three integration surfaces:

1. **Python API** for embedding the pipeline in custom automation.
2. **Command-line interface (CLI)** that mirrors the Python capabilities.
3. **FastMCP(stdio) server** for Model Context Protocol hosts.

This guide walks through the full setup flow and provides runnable samples for
each entry point.

## Prerequisites

- Python 3.13 or newer
- [`uv`](https://github.com/astral-sh/uv) for dependency management
- (Optional) [`fastmcp`](https://pypi.org/project/fastmcp/) or any MCP host to
  exercise the FastMCP tools

Clone the repository and install dependencies:

```bash
git clone https://github.com/<your-org>/yuragi.git
cd yuragi
uv sync
```

> **Note**: The project relies exclusively on `uv`. Avoid using `pip install` or
> other package managers inside the virtual environment.

After syncing dependencies you can validate the environment with the provided
Nox sessions:

```bash
uv run nox -s lint
uv run nox -s typing
uv run nox -s test
```

## Sample data

The `samples/` directory contains fixtures that make every integration path
deterministic:

| File | Purpose |
| --- | --- |
| `samples/crud_requests.json` | Example normalization requests consumed by Python/CLI/MCP. |
| `samples/repo_hits.json` | Repository search hits that emulate a ripgrep run. |
| `samples/db_fixture.json` | Database introspection payload used during verification. |
| `samples/glossary.json` | Glossary illustrating service/table/column aliases. |
| `samples/python_api_example.py` | End-to-end Python sample that generates `graph_python.json`. |

Feel free to copy these fixtures and adapt them to match your environment or
data sources.

## Python API usage

The quickest way to experiment with the pipeline is to run the bundled Python
example:

```bash
uv run python samples/python_api_example.py
```

The script performs the following steps:

1. Loads the sample CRUD requests and converts them into
   `NormalizationRequest` objects.
2. Builds repository and database adapters using the deterministic fixtures.
3. Instantiates `CrudWorkflowOrchestrator` and wraps it in
   `CrudNormalizationPipeline`.
4. Writes a validated dependency graph to `graph_python.json` in the current
   directory.

To embed the library inside another application you can mirror the structure of
`samples/python_api_example.py`: create `NormalizeAgent` and `VerifyAgent`
instances, wire them into `CrudWorkflowOrchestrator`, and call
`CrudNormalizationPipeline.run()` with your own requests.

### Minimal inline example

The same logic can be executed inline without the helper script:

```bash
uv run python <<'PY'
from pathlib import Path
import json

from yuragi.agents import CrudWorkflowOrchestrator, NormalizeAgent, NormalizationRequest, TermGlossary, VerifyAgent
from yuragi.interfases.mcp.server_fastmcp import DatabaseFixturePayload, DatabaseOptions, RepoFixturePayload, RepoOptions
from yuragi.pipelines import CrudNormalizationPipeline, PipelineOutput, PipelineOutputFormat

base_dir = Path("samples")
requests_data = json.loads((base_dir / "crud_requests.json").read_text())
repo_fixture = RepoFixturePayload.model_validate_json((base_dir / "repo_hits.json").read_text())
db_fixture = DatabaseFixturePayload.model_validate_json((base_dir / "db_fixture.json").read_text())
glossary_data = json.loads((base_dir / "glossary.json").read_text())

requests = [
    NormalizationRequest(
        description=item["description"],
        service=item.get("service"),
        table_hint=item.get("table_hint"),
        columns_hint=tuple(item.get("columns_hint", [])),
        where_hint=tuple(item.get("where_hint", [])),
        path=item.get("path"),
        span=item.get("span"),
    )
    for item in requests_data
]

glossary = TermGlossary(
    service_aliases=glossary_data.get("service_aliases", {}),
    table_aliases=glossary_data.get("table_aliases", {}),
    column_aliases=glossary_data.get("column_aliases", {}),
)

repo_options = RepoOptions(fixture=repo_fixture)
repository, base_query = repo_options.build()
db_options = DatabaseOptions(fixture=db_fixture, schema="public")
database, schema = db_options.build()

orchestrator = CrudWorkflowOrchestrator(
    normalize_agent=NormalizeAgent(),
    verify_agent=VerifyAgent(repository=repository, database=database),
)

pipeline = CrudNormalizationPipeline(orchestrator=orchestrator)
graph = pipeline.run(
    requests,
    default_service="BillingService",
    glossary_overrides=glossary,
    repo_base_query=base_query,
    schema=schema,
    outputs=[PipelineOutput(format=PipelineOutputFormat.JSON, path=Path("graph_python.json"))],
)

print(graph.model_dump_json(indent=2))
PY
```

The printed JSON matches the content of `graph_python.json`.

## Command-line interface (CLI)

The CLI mirrors the Python API and is exposed via the `yuragi` entry point. Use
`uv run` to execute it inside the managed environment:

```bash
uv run yuragi --help
```

### Normalize descriptions

```bash
uv run yuragi normalize --input samples/crud_requests.json --glossary samples/glossary.json
```

The command prints a `CRUDActionList` to stdout. Use `--output` to persist the
result to disk.

### Export the graph schema

```bash
uv run yuragi schema export --out schema.json
```

This writes the JSON Schema describing graph payloads. Set `--out -` (default)
to stream the schema to stdout.

### Run the CRUD pipeline end-to-end

```bash
uv run yuragi run-crud-pipeline \
  --requests samples/crud_requests.json \
  --repo-fixture samples/repo_hits.json \
  --db-fixture samples/db_fixture.json \
  --schema public \
  --default-service BillingService \
  --glossary samples/glossary.json \
  --out graph_cli.json
```

The fixtures emulate repository/database evidence so the command succeeds
without external dependencies. Failures are emitted as JSON on stderr along with
non-zero exit codes.

### Configure repository allowlists

CLI repository searches are restricted to a predefined command allowlist. By
default only `rg` is permitted. Extend or replace the allowlist by setting the
`YURAGI_REPO_ALLOW_CMDS` environment variable before invoking the CLI:

```bash
export YURAGI_REPO_ALLOW_CMDS="rg,git"
uv run yuragi run-crud-pipeline --requests ... --repo-command git
```

For shared environments, place the configuration in a JSON file. The CLI looks
for `~/.config/yuragi/config.json` and can be pointed to an alternate location
via the `YURAGI_CLI_CONFIG` environment variable. The file must be a JSON
object; set `repo_allowed_commands` to either a comma-delimited string or an
array of strings:

```json
{
  "repo_allowed_commands": ["rg", "git"]
}
```

If both the environment variable and the configuration file are present, the
environment variable wins.

## FastMCP(stdio) server

The FastMCP exposure publishes the same capabilities as MCP tools. Set the
`YURAGI_EXPOSE` environment variable to `mcp` and run the package entry point:

```bash
YURAGI_EXPOSE=mcp uv run python -m yuragi
```

By default the server starts in stdio mode and prints a banner confirming that
the tools were registered. Use an MCP-compatible host (for example the
[`fastmcp` CLI](https://github.com/fastmcp/fastmcp)) to connect:

```bash
# In a separate terminal while the server is running
uv run fastmcp dev-client --stdio
```

Once connected, the following tools are available:

- `yuragi_normalize_crud`
- `yuragi_verify_crud`
- `yuragi_run_crud_pipeline`
- `yuragi_spec_impact`
- `yuragi_merge_graphs`

Each tool accepts the same payloads used in the Python/CLI samples. Point the
client to the fixtures in `samples/` for deterministic responses (for example,
pass `repo_opts.fixture.hits` from `samples/repo_hits.json`).

Database verification in FastMCP runs against fixtures by default. Servers may
optionally supply a `database_allowlist` during startup, enabling clients to
request one of the pre-approved presets via `db_opts.preset`. Custom
`engine`/`dsn`/`database` values from the client are rejected to prevent
untrusted connections.

To switch back to the CLI exposure, either unset `YURAGI_EXPOSE` or set it to
`cli` before running `python -m yuragi`.

## Glossary structure

The glossary maps ambiguous terminology to canonical names. The
`samples/glossary.json` file demonstrates the expected structure:

```json
{
  "service_aliases": {
    "billing": "BillingService"
  },
  "table_aliases": {
    "billing ledger": "billing_ledger",
    "ledger": "billing_ledger"
  },
  "column_aliases": {
    "checkout total": "checkout_total"
  }
}
```

Provide this payload via the Python API (`TermGlossary`) or the CLI/MCP
interfaces (`--glossary` flag or `NormalizationHintsPayload`) to steer
normalization toward your canonical names.

## Next steps

- Replace the fixtures with real repository/database integrations by supplying
  `CLIAdapter` or `create_database_adapter` parameters.
- Extend the pipeline by adding custom tools or adjusting the scoring logic in
  `yuragi/core/scoring.py`.
- Integrate with your MCP host, observability stack, or deployment pipelines as
  needed.
