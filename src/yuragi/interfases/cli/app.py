"""Command-line interface for the yuragi project."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast
from collections.abc import Iterable, Mapping, MutableMapping, Sequence

from pydantic import ValidationError

from yuragi.agents import (
    CrudWorkflowOrchestrator,
    NormalizationRequest,
    NormalizeAgent,
    OrchestrationError,
    TermGlossary,
    VerifyAgent,
)
# The CLI surfaces high-level entry points and therefore imports orchestration
# utilities lazily to avoid leaking lower-level implementation details.
from yuragi.core.safety import mask_pii, scrub_for_logging
from yuragi.core.schema import build_graph_json_schema
from yuragi.pipelines import CrudNormalizationPipeline, PipelineOutput, PipelineOutputFormat
from yuragi.tools.db import (
    ColumnIntrospectionResult,
    ColumnMetadata,
    DatabaseAdapter,
    DatabaseToolError,
    ExplainPlanResult,
    TableIntrospectionResult,
    create_database_adapter,
    NEGATIVE_RESULT_CONFIDENCE_DELTA,
)
from yuragi.tools.repo import (
    CLIAdapter,
    CallableAdapter,
    RepoAdapterError,
    RepoHit,
    RepositorySearcher,
    SearchQuery,
)


class CliError(RuntimeError):
    """Exception raised for anticipated CLI failures."""

    def __init__(self, message: str, *, exit_code: int = 1, details: Any | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.details = details


def main(argv: Sequence[str] | None = None) -> int:
    """Parse *argv*, execute the requested command and return the exit code."""
    parser = _build_parser()
    args_namespace = parser.parse_args(argv)
    command = getattr(args_namespace, "command", None)
    if command is None:
        parser.print_help()
        return 1
    try:
        exit_code = command(args_namespace)
    except CliError as error:
        _emit_error(error)
        exit_code = error.exit_code
    except KeyboardInterrupt as error:  # pragma: no cover - manual interruption
        cli_error = CliError("Aborted by user", exit_code=130, details=str(error))
        _emit_error(cli_error)
        exit_code = cli_error.exit_code
    except ValidationError as error:
        cli_error = CliError("Invalid payload", exit_code=1, details=error.errors())
        _emit_error(cli_error)
        exit_code = cli_error.exit_code
    except (RepoAdapterError, DatabaseToolError, OrchestrationError) as error:
        cli_error = CliError(str(error), exit_code=1)
        _emit_error(cli_error)
        exit_code = cli_error.exit_code
    except Exception as error:  # pragma: no cover - defensive guard
        cli_error = CliError("Unexpected error", exit_code=1, details=str(error))
        _emit_error(cli_error)
        exit_code = cli_error.exit_code
    return exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yuragi",
        description="Normalize ambiguous CRUD activity into verified dependency graphs.",
    )
    parser.add_argument("--version", action="version", version="yuragi 0.1.0")
    subparsers = parser.add_subparsers(dest="command_name")

    _configure_normalize(subparsers)
    _configure_schema(subparsers)
    _configure_run_crud_pipeline(subparsers)

    return parser


def _configure_normalize(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "normalize",
        help="Normalize ambiguous CRUD descriptions into structured actions.",
    )
    parser.set_defaults(command=_command_normalize)
    parser.add_argument(
        "--input",
        "--in",
        dest="input_path",
        default="-",
        help="Path to a JSON file containing normalization requests (default: stdin).",
    )
    parser.add_argument(
        "--output",
        "--out",
        dest="output_path",
        help="Optional path to write the normalized CRUD actions as JSON.",
    )
    parser.add_argument(
        "--default-service",
        dest="default_service",
        help="Service name to fall back to when requests omit it.",
    )
    parser.add_argument(
        "--glossary",
        dest="glossary_path",
        help="JSON file describing service/table/column aliases.",
    )


def _configure_schema(subparsers: Any) -> None:
    schema_parser = subparsers.add_parser(
        "schema",
        help="Interact with the graph JSON Schema utilities.",
    )
    schema_subparsers = schema_parser.add_subparsers(dest="schema_command")

    export_parser = schema_subparsers.add_parser(
        "export",
        help="Export the Graph JSON Schema to a file (default: stdout).",
    )
    export_parser.set_defaults(command=_command_schema_export)
    export_parser.add_argument(
        "--output",
        "--out",
        dest="output_path",
        default="-",
        help="Destination file for the JSON Schema or '-' for stdout.",
    )


def _configure_run_crud_pipeline(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "run-crud-pipeline",
        help="Execute the CRUD normalization pipeline and emit a graph.",
    )
    parser.set_defaults(command=_command_run_crud_pipeline)
    parser.add_argument(
        "--requests",
        "--input",
        dest="requests_path",
        required=True,
        help="Path to the JSON file describing normalization requests.",
    )
    parser.add_argument(
        "--default-service",
        dest="default_service",
        help="Service name applied when requests omit it.",
    )
    parser.add_argument(
        "--schema",
        dest="database_schema",
        help="Database schema namespace passed to verification.",
    )
    parser.add_argument(
        "--glossary",
        dest="glossary_path",
        help="JSON file describing service/table/column aliases.",
    )
    parser.add_argument(
        "--repo-fixture",
        dest="repo_fixture",
        help="JSON fixture mapping search patterns to repository hits.",
    )
    parser.add_argument(
        "--repo-command",
        "--repo-cmd",
        dest="repo_command",
        help=(
            "Executable used for repository searches (e.g. 'rg'). Allowed commands "
            "are configured via the YURAGI_REPO_ALLOW_CMDS environment variable or "
            "the CLI configuration file."
        ),
    )
    parser.add_argument(
        "--repo-arg",
        dest="repo_args",
        action="append",
        default=[],
        help="Additional argument forwarded to the repository CLI adapter.",
    )
    parser.add_argument(
        "--repo-path",
        dest="repo_paths",
        action="append",
        default=[],
        help="Limit searches to a path (can be repeated).",
    )
    parser.add_argument(
        "--repo-flag",
        dest="repo_flags",
        action="append",
        default=[],
        help="Additional CLI flags passed to the repository adapter base query.",
    )
    parser.add_argument(
        "--repo-pattern",
        dest="repo_pattern",
        default="",
        help="Base pattern for repository searches before candidate overrides.",
    )
    parser.add_argument(
        "--repo-context-lines",
        dest="repo_context_lines",
        type=int,
        default=0,
        help="Number of context lines captured by repository searches.",
    )
    parser.add_argument(
        "--repo-timeout",
        dest="repo_timeout",
        type=float,
        default=10.0,
        help="Timeout (seconds) applied to CLI repository commands.",
    )
    parser.add_argument(
        "--repo-cwd",
        dest="repo_cwd",
        help="Working directory for CLI repository searches.",
    )
    parser.add_argument(
        "--db-engine",
        dest="db_engine",
        help="Database engine identifier (e.g. 'sqlite', 'postgres').",
    )
    parser.add_argument(
        "--db-database",
        dest="db_database",
        help="Database path for SQLite adapters.",
    )
    parser.add_argument(
        "--db-dsn",
        dest="db_dsn",
        help="Connection string for Postgres adapters.",
    )
    parser.add_argument(
        "--db-uri",
        dest="db_uri",
        action="store_true",
        help="Interpret the SQLite database path as a URI.",
    )
    parser.add_argument(
        "--db-fixture",
        dest="db_fixture",
        help="JSON fixture describing database introspection results.",
    )
    parser.add_argument(
        "--out",
        dest="output_path",
        help="Path to write the resulting graph as JSON.",
    )
    parser.add_argument(
        "--ndjson-out",
        dest="ndjson_output_path",
        help="Optional NDJSON output path containing graph, nodes, and edges.",
    )


def _command_normalize(args: argparse.Namespace) -> int:
    requests = _load_requests(Path(args.input_path))
    glossary = _load_glossary(args.glossary_path)

    agent = NormalizeAgent()
    normalized = agent.normalize(
        requests,
        default_service=args.default_service,
        glossary_overrides=glossary,
    )

    _write_json_output(normalized.model_dump(mode="json"), args.output_path)
    return 0


def _command_schema_export(args: argparse.Namespace) -> int:
    schema = build_graph_json_schema()
    _write_json_output(schema, args.output_path)
    return 0


def _command_run_crud_pipeline(args: argparse.Namespace) -> int:
    if args.repo_fixture and args.repo_command:
        message = "Specify either --repo-fixture or --repo-command, not both."
        raise CliError(message)
    if not args.repo_fixture and not args.repo_command:
        message = "One of --repo-fixture or --repo-command is required."
        raise CliError(message)

    requests = _load_requests(Path(args.requests_path))
    glossary = _load_glossary(args.glossary_path)
    repository = _build_repository(args)
    database = _build_database(args)

    orchestrator = CrudWorkflowOrchestrator(
        normalize_agent=NormalizeAgent(),
        verify_agent=VerifyAgent(repository=repository, database=database),
    )
    pipeline = CrudNormalizationPipeline(orchestrator=orchestrator)

    outputs: list[PipelineOutput] = []
    if args.output_path:
        outputs.append(PipelineOutput(PipelineOutputFormat.JSON, Path(args.output_path)))
    if args.ndjson_output_path:
        outputs.append(
            PipelineOutput(PipelineOutputFormat.NDJSON, Path(args.ndjson_output_path)),
        )

    base_query = SearchQuery(
        pattern=args.repo_pattern,
        paths=tuple(args.repo_paths),
        flags=tuple(args.repo_flags),
        context_lines=args.repo_context_lines,
    )

    graph = pipeline.run(
        requests,
        default_service=args.default_service,
        glossary_overrides=glossary,
        repo_base_query=base_query,
        schema=args.database_schema,
        outputs=outputs,
    )

    _write_json_output(graph.model_dump(mode="json"), None)
    return 0


def _load_requests(path: Path) -> list[NormalizationRequest]:
    raw_data = _read_json(path)
    requests_data: object
    if isinstance(raw_data, Mapping) and "requests" in raw_data:
        requests_data = cast("object", raw_data["requests"])
    else:
        requests_data = cast("object", raw_data)

    if not isinstance(requests_data, Sequence) or isinstance(
        requests_data, (str, bytes, bytearray),
    ):
        message = "Normalization requests must be a JSON array."
        raise CliError(message)

    requests: list[NormalizationRequest] = []
    normalized_requests = cast("Sequence[object]", requests_data)
    for entry in normalized_requests:
        if isinstance(entry, NormalizationRequest):
            requests.append(entry)
            continue
        if not isinstance(entry, Mapping):
            message = "Each request must be a JSON object."
            raise CliError(message)
        mapping_entry = cast("Mapping[str, object]", entry)
        request = _build_request(mapping_entry)
        requests.append(request)
    if not requests:
        message = "At least one normalization request is required."
        raise CliError(message)
    return requests


def _build_request(entry: Mapping[str, object]) -> NormalizationRequest:
    description = entry.get("description")
    if not isinstance(description, str):
        message = "Each request must include a string 'description' field."
        raise CliError(message)

    service = _optional_str_field(entry, "service", "Request")
    table_hint = _optional_str_field(entry, "table_hint", "Request")
    columns_hint = _string_sequence_field(entry, "columns_hint", "Request")
    where_hint = _string_sequence_field(entry, "where_hint", "Request")
    path = _optional_str_field(entry, "path", "Request")
    span = _optional_str_field(entry, "span", "Request")

    return NormalizationRequest(
        description=description,
        service=service,
        table_hint=table_hint,
        columns_hint=list(columns_hint),
        where_hint=list(where_hint),
        path=path,
        span=span,
    )


def _optional_str_field(
    mapping: Mapping[str, object], key: str, context: str,
) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    message = f"{context} must provide a string value for '{key}'."
    raise CliError(message)


def _string_sequence_field(
    mapping: Mapping[str, object], key: str, context: str,
) -> tuple[str, ...]:
    raw = mapping.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, Iterable):
        message = f"{context} must provide an iterable of strings for '{key}'."
        raise CliError(message)
    items: list[str] = []
    for entry in cast("Iterable[object]", raw):
        if not isinstance(entry, str):
            message = f"{context} must contain only strings in '{key}'."
            raise CliError(message)
        if entry:
            items.append(entry)
    return tuple(items)


def _load_glossary(glossary_path: str | None) -> TermGlossary | None:
    if not glossary_path:
        return None
    data = _read_json(Path(glossary_path))
    if not isinstance(data, Mapping):
        message = "Glossary payload must be a JSON object."
        raise CliError(message)
    mapping_data = cast("Mapping[str, object]", data)
    service_aliases = _coerce_str_mapping(mapping_data.get("service_aliases", {}))
    table_aliases = _coerce_str_mapping(mapping_data.get("table_aliases", {}))
    column_aliases = _coerce_str_mapping(mapping_data.get("column_aliases", {}))
    return TermGlossary(
        service_aliases=service_aliases,
        table_aliases=table_aliases,
        column_aliases=column_aliases,
    )


def _coerce_str_mapping(value: object) -> MutableMapping[str, str]:
    if not isinstance(value, Mapping):
        message = "Alias sections must be JSON objects."
        raise CliError(message)
    mapping_value = cast("Mapping[str, object]", value)
    coerced: dict[str, str] = {}
    for key, raw in mapping_value.items():
        if not isinstance(raw, str):
            message = "Glossary aliases must map strings to strings."
            raise CliError(message)
        coerced[key] = raw
    return coerced


def _build_repository(args: argparse.Namespace) -> RepositorySearcher:
    repo_fixture = getattr(args, "repo_fixture", None)
    if isinstance(repo_fixture, str) and repo_fixture:
        return _build_fixture_repository(Path(repo_fixture))
    command = _build_repo_command(args)
    allowed = resolve_repo_allowed_commands()
    cwd: Path | None = None
    repo_cwd = getattr(args, "repo_cwd", None)
    if isinstance(repo_cwd, str) and repo_cwd:
        cwd = Path(repo_cwd).resolve()
    try:
        adapter = CLIAdapter(
            command,
            allowed_commands=allowed,
            runner=subprocess.run,
            cwd=cwd,
            timeout=args.repo_timeout,
        )
    except ValueError as error:
        message = str(error)
        raise CliError(message) from error
    return RepositorySearcher(adapter)


def _build_repo_command(args: argparse.Namespace) -> list[str]:
    repo_command = getattr(args, "repo_command", None)
    if not isinstance(repo_command, str) or not repo_command:
        message = "--repo-command is required when no fixture is provided."
        raise CliError(message)
    command: list[str] = [repo_command]
    raw_args = cast("Iterable[str] | None", getattr(args, "repo_args", None))
    if raw_args:
        command.extend(list(raw_args))
    return command


def resolve_repo_allowed_commands() -> set[str]:
    env_value = os.environ.get("YURAGI_REPO_ALLOW_CMDS")
    if env_value:
        return _normalize_allowlist(env_value)
    config = _load_cli_config()
    if config is not None:
        raw_allowed = config.get("repo_allowed_commands")
        if raw_allowed is not None:
            return _normalize_allowlist(raw_allowed)
    return {"rg"}


def _normalize_allowlist(raw_value: object) -> set[str]:
    if isinstance(raw_value, str):
        candidates = raw_value.split(",")
    elif isinstance(raw_value, Iterable):
        candidates: list[str] = []
        for item in cast("Iterable[object]", raw_value):
            if not isinstance(item, str):
                message = "Allowed commands must be strings"
                raise CliError(message)
            candidates.append(item)
    else:
        message = "Allowed commands must be provided as a string or sequence of strings"
        raise CliError(message)

    allowed = {item.strip() for item in candidates if item.strip()}
    if not allowed:
        message = "At least one allowed command must be specified"
        raise CliError(message)
    return allowed


def _load_cli_config() -> Mapping[str, Any] | None:
    config_env = os.environ.get("YURAGI_CLI_CONFIG")
    config_path = (
        Path(config_env).expanduser()
        if config_env
        else Path.home() / ".config" / "yuragi" / "config.json"
    )

    if not config_path.exists():
        return None

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as error:
        message = f"Failed to read CLI configuration from {config_path}: {error}"
        raise CliError(message) from error

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        message = f"Failed to parse CLI configuration from {config_path}: {error}"
        raise CliError(message) from error

    if not isinstance(payload, Mapping):
        message = f"CLI configuration at {config_path} must be a JSON object"
        raise CliError(message)

    return cast("Mapping[str, Any]", payload)


def _build_fixture_repository(path: Path) -> RepositorySearcher:
    raw_data = _read_json(path)
    mapping_data: object = raw_data
    if isinstance(raw_data, Mapping) and "hits" in raw_data:
        mapping_data = cast("object", raw_data["hits"])
    if not isinstance(mapping_data, Mapping):
        message = "Repository fixture must be a JSON object mapping patterns to hits."
        raise CliError(message)

    pattern_map: dict[str, list[RepoHit]] = {}
    hits_mapping = cast("Mapping[str, object]", mapping_data)
    for key, value in hits_mapping.items():
        if not isinstance(value, Sequence):
            message = f"Repository fixture for pattern '{key}' must be a list of hits."
            raise CliError(message)
        entries = cast("Sequence[object]", value)
        hits = [RepoHit.model_validate(item) for item in entries]
        pattern_map[key] = hits

    def _search(query: SearchQuery) -> list[RepoHit]:
        return list(pattern_map.get(query.pattern, []))

    return RepositorySearcher(CallableAdapter(_search))


def _build_database(args: argparse.Namespace) -> DatabaseAdapter:
    db_fixture = getattr(args, "db_fixture", None)
    if isinstance(db_fixture, str) and db_fixture:
        return _FixtureDatabaseAdapter(Path(db_fixture))
    engine = getattr(args, "db_engine", None)
    if not isinstance(engine, str) or not engine:
        message = "Either --db-fixture or --db-engine is required to configure the database."
        raise CliError(message)
    return create_database_adapter(
        engine,
        database=getattr(args, "db_database", None),
        dsn=getattr(args, "db_dsn", None),
        uri=bool(getattr(args, "db_uri", False)),
    )


class _FixtureDatabaseAdapter(DatabaseAdapter):
    """Database adapter backed by JSON fixtures for deterministic demos."""

    def __init__(self, path: Path) -> None:
        data = _read_json(path)
        if not isinstance(data, Mapping):
            message = "Database fixture must be a JSON object."
            raise CliError(message)
        mapping_data = cast("Mapping[str, object]", data)
        self._tables = _coerce_table_fixtures(mapping_data.get("tables", {}))
        self._columns = _coerce_column_fixtures(mapping_data.get("columns", {}))

    def introspect_table(self, table: str, *, schema: str | None = None) -> TableIntrospectionResult:
        key = (schema or "", table)
        if key in self._tables:
            return self._tables[key]
        fallback_schema = schema if schema else None
        return TableIntrospectionResult(
            table=table,
            schema=fallback_schema,
            exists=False,
            row_count=None,
            confidence_delta=NEGATIVE_RESULT_CONFIDENCE_DELTA,
        )

    def introspect_columns(self, table: str, *, schema: str | None = None) -> ColumnIntrospectionResult:
        key = (schema or "", table)
        if key in self._columns:
            return self._columns[key]
        fallback_schema = schema if schema else None
        return ColumnIntrospectionResult(
            table=table,
            schema=fallback_schema,
            exists=False,
            columns=(),
            confidence_delta=NEGATIVE_RESULT_CONFIDENCE_DELTA,
        )

    def explain(self, sql: str) -> ExplainPlanResult:  # pragma: no cover - not used in CLI fixtures
        del sql
        message = "EXPLAIN is not available for fixture-backed adapters"
        raise DatabaseToolError(message)


def _coerce_table_fixtures(value: object) -> dict[tuple[str, str], TableIntrospectionResult]:
    mapping_value = _expect_mapping(
        value, message="Database fixture 'tables' section must be an object.",
    )
    fixtures: dict[tuple[str, str], TableIntrospectionResult] = {}
    for key, raw in mapping_value.items():
        raw_mapping = _expect_mapping(
            raw, message="Each table fixture must map a string key to an object.",
        )
        schema = _coerce_optional_str_value(
            raw_mapping.get("schema"),
            message="Table fixture schema must be a string when provided.",
        )
        exists = bool(raw_mapping.get("exists", True))
        row_count = _coerce_optional_int(
            raw_mapping.get("row_count"),
            message="Table fixture row_count must be an integer when provided.",
        )
        confidence_delta = _coerce_confidence_delta(
            raw_mapping.get("confidence_delta"),
            exists=exists,
            message="Table fixture confidence_delta must be numeric when provided.",
        )
        fixtures[(schema or "", key)] = TableIntrospectionResult(
            table=key,
            schema=schema,
            exists=exists,
            row_count=row_count,
            confidence_delta=confidence_delta,
        )
    return fixtures


def _coerce_column_fixtures(value: object) -> dict[tuple[str, str], ColumnIntrospectionResult]:
    mapping_value = _expect_mapping(
        value, message="Database fixture 'columns' section must be an object.",
    )
    fixtures: dict[tuple[str, str], ColumnIntrospectionResult] = {}
    for table, raw in mapping_value.items():
        raw_mapping = _expect_mapping(
            raw, message="Each column fixture must map a string key to an object.",
        )
        schema = _coerce_optional_str_value(
            raw_mapping.get("schema"),
            message="Column fixture schema must be a string when provided.",
        )
        exists = bool(raw_mapping.get("exists", True))
        columns = _coerce_column_metadata_sequence(
            raw_mapping.get("columns", []),
            table_name=table,
        )
        confidence_delta = _coerce_confidence_delta(
            raw_mapping.get("confidence_delta"),
            exists=exists,
            message="Column fixture confidence_delta must be numeric when provided.",
        )
        result = ColumnIntrospectionResult(
            table=table,
            schema=schema,
            exists=exists,
            columns=columns,
            confidence_delta=confidence_delta,
        )
        fixtures[(schema or "", table)] = result
    return fixtures


def _expect_mapping(value: object, *, message: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CliError(message)
    return cast("Mapping[str, object]", value)


def _coerce_optional_str_value(value: object, *, message: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise CliError(message)


def _coerce_optional_int(value: object, *, message: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    raise CliError(message)


def _coerce_confidence_delta(
    value: object, *, exists: bool, message: str,
) -> float:
    if value is None:
        return 0.0 if exists else NEGATIVE_RESULT_CONFIDENCE_DELTA
    if isinstance(value, (int, float)):
        return float(value)
    raise CliError(message)


def _coerce_column_metadata_sequence(
    value: object, *, table_name: str,
) -> tuple[ColumnMetadata, ...]:
    if value is None:
        return ()
    if not isinstance(value, Iterable):
        message = (
            "Column fixture 'columns' must be an iterable of objects "
            f"for table '{table_name}'."
        )
        raise CliError(message)
    columns: list[ColumnMetadata] = []
    for item in cast("Iterable[object]", value):
        mapping_item = _expect_mapping(
            item, message="Column fixture entries must be objects.",
        )
        name_obj = mapping_item.get("name")
        data_type_obj = mapping_item.get("data_type")
        if not isinstance(name_obj, str) or not isinstance(data_type_obj, str):
            message = "Column fixture entries require string 'name' and 'data_type'."
            raise CliError(message)
        nullable = bool(mapping_item.get("nullable", False))
        default_obj = mapping_item.get("default")
        default_value = (
            default_obj
            if default_obj is None or isinstance(default_obj, str)
            else str(default_obj)
        )
        columns.append(
            ColumnMetadata(
                name=name_obj,
                data_type=data_type_obj,
                nullable=nullable,
                default=default_value,
            ),
        )
    return tuple(columns)


def _read_json(path: Path) -> object:
    if str(path) == "-":
        try:
            return cast("object", json.load(sys.stdin))
        except json.JSONDecodeError as error:
            message = f"Failed to parse JSON from stdin: {error}"
            raise CliError(message) from error
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        message = f"File not found: {path}"
        raise CliError(message) from error
    except OSError as error:
        message = f"Unable to read {path}: {error}"  # pragma: no cover - defensive guard
        raise CliError(message) from error
    try:
        return cast("object", json.loads(text))
    except json.JSONDecodeError as error:
        message = f"Failed to parse JSON from {path}: {error}"
        raise CliError(message) from error


def _write_json_output(payload: Any, output_path: str | None) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if output_path in {None, "", "-"}:
        sys.stdout.write(serialized + "\n")
        sys.stdout.flush()
        return
    path = Path(cast("str", output_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized + "\n", encoding="utf-8")


def _emit_error(error: CliError) -> None:
    payload = {
        "status": "error",
        "message": mask_pii(str(error)),
        "type": type(error).__name__,
    }
    if error.details is not None:
        payload["details"] = scrub_for_logging(error.details)
    safe_payload = scrub_for_logging(payload)
    serialized = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True)
    sys.stderr.write(serialized + "\n")
    sys.stderr.flush()


__all__ = ["CLIExposure", "main"]


class CLIExposure:
    """Exposure adapter that delegates to the CLI entry point."""

    def serve(self, *, config: Mapping[str, Any] | None = None) -> None:
        """Execute the CLI using the provided configuration."""
        argv: Sequence[str] | None = None
        if config is not None and "argv" in config:
            raw_argv = config["argv"]
            if raw_argv is not None:
                if not isinstance(raw_argv, Sequence) or isinstance(raw_argv, (str, bytes)):
                    message = "config['argv'] must be a sequence of strings"
                    raise TypeError(message)
                sequence_candidate = cast("Sequence[Any]", raw_argv)
                validated_arguments: list[str] = []
                for argument in sequence_candidate:
                    if not isinstance(argument, str):
                        message = "config['argv'] must contain only strings"
                        raise TypeError(message)
                    validated_arguments.append(argument)
                argv = list(validated_arguments)
        exit_code = main(argv)
        raise SystemExit(exit_code)
