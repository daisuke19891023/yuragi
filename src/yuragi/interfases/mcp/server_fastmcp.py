"""FastMCP(stdio) exposure implementation for yuragi."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from collections.abc import Mapping, Sequence

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from yuragi.agents import (
    CrudWorkflowOrchestrator,
    NormalizationRequest,
    NormalizeAgent,
    TermGlossary,
    VerifyAgent,
)
from yuragi.core.models import CRUDActionList, Edge, Graph, Node
from yuragi.pipelines import CrudNormalizationPipeline
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
from yuragi.tools.repo import CallableAdapter, RepoHit, RepositorySearcher, SearchQuery
from yuragi.tools.specs import SpecChange, build_spec_impact_graph


class CodeSnippetPayload(BaseModel):
    """Describe a code snippet that should be normalised."""

    model_config = ConfigDict(extra="forbid")

    description: str
    service: str | None = None
    table_hint: str | None = None
    columns_hint: list[str] = Field(default_factory=list)
    where_hint: list[str] = Field(default_factory=list)
    path: str | None = None
    span: str | None = None

    def to_request(self) -> NormalizationRequest:
        """Convert the payload into a :class:`NormalizationRequest`."""
        return NormalizationRequest(
            description=self.description,
            service=self.service,
            table_hint=self.table_hint,
            columns_hint=list(self.columns_hint),
            where_hint=list(self.where_hint),
            path=self.path,
            span=self.span,
        )


class GlossaryPayload(BaseModel):
    """Payload carrying alias overrides for the normalization glossary."""

    model_config = ConfigDict(extra="forbid")

    service_aliases: dict[str, str] = Field(default_factory=dict)
    table_aliases: dict[str, str] = Field(default_factory=dict)
    column_aliases: dict[str, str] = Field(default_factory=dict)

    def to_term_glossary(self) -> TermGlossary:
        """Return a :class:`TermGlossary` built from the payload."""
        return TermGlossary(
            service_aliases=dict(self.service_aliases),
            table_aliases=dict(self.table_aliases),
            column_aliases=dict(self.column_aliases),
        )


class NormalizationHintsPayload(BaseModel):
    """Optional hints that influence CRUD normalization."""

    model_config = ConfigDict(extra="forbid")

    default_service: str | None = None
    glossary: GlossaryPayload | None = None

    def to_kwargs(self) -> tuple[str | None, TermGlossary | None]:
        """Convert the hints into arguments for :class:`NormalizeAgent`."""
        glossary = self.glossary.to_term_glossary() if self.glossary else None
        return self.default_service, glossary


class SearchQueryPayload(BaseModel):
    """Description of the base repository search query."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = ""
    paths: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    context_lines: int = 0

    def to_query(self) -> SearchQuery:
        """Return a :class:`SearchQuery` instance."""
        return SearchQuery(
            pattern=self.pattern,
            paths=tuple(self.paths),
            flags=tuple(self.flags),
            context_lines=self.context_lines,
        )


def _empty_repo_hits() -> dict[str, list[RepoHit]]:
    return {}


class RepoFixturePayload(BaseModel):
    """Fixture describing deterministic repository search results."""

    model_config = ConfigDict(extra="forbid")

    hits: dict[str, list[RepoHit]] = Field(default_factory=_empty_repo_hits)

    def build_searcher(self) -> RepositorySearcher:
        """Return a :class:`RepositorySearcher` backed by the fixture."""
        pattern_map = {
            pattern: [hit.model_copy(deep=True) for hit in hits]
            for pattern, hits in self.hits.items()
        }

        def _search(query: SearchQuery) -> list[RepoHit]:
            return [hit.model_copy(deep=True) for hit in pattern_map.get(query.pattern, [])]

        return RepositorySearcher(CallableAdapter(_search))


class RepoOptions(BaseModel):
    """Options controlling repository verification behaviour."""

    model_config = ConfigDict(extra="forbid")

    base_query: SearchQueryPayload | None = None
    fixture: RepoFixturePayload | None = None

    def build(self) -> tuple[RepositorySearcher, SearchQuery]:
        """Return a searcher and its associated base query."""
        searcher = (
            self.fixture.build_searcher() if self.fixture else RepositorySearcher(CallableAdapter(lambda _: []))
        )
        base_query = (self.base_query or SearchQueryPayload()).to_query()
        return searcher, base_query


class ColumnMetadataPayload(BaseModel):
    """Fixture describing a database column."""

    model_config = ConfigDict(extra="forbid")

    name: str
    data_type: str = "text"
    nullable: bool = True
    default: str | None = None

    def to_metadata(self) -> ColumnMetadata:
        """Convert the payload into :class:`ColumnMetadata`."""
        return ColumnMetadata(
            name=self.name,
            data_type=self.data_type,
            nullable=self.nullable,
            default=self.default,
        )


def _empty_column_metadata_payloads() -> list[ColumnMetadataPayload]:
    return []


class TableFixturePayload(BaseModel):
    """Fixture describing table introspection results."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str | None = Field(default=None, alias="schema")
    exists: bool = True
    row_count: int | None = None
    confidence_delta: float | None = None

    def to_result(self, table: str) -> TableIntrospectionResult:
        """Return a :class:`TableIntrospectionResult` instance."""
        delta = self.confidence_delta
        if delta is None:
            delta = 0.0 if self.exists else NEGATIVE_RESULT_CONFIDENCE_DELTA
        return TableIntrospectionResult(
            table=table,
            schema=self.schema_name,
            exists=self.exists,
            row_count=self.row_count,
            confidence_delta=delta,
        )


class ColumnFixturePayload(BaseModel):
    """Fixture describing table column introspection results."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: str | None = Field(default=None, alias="schema")
    exists: bool = True
    columns: list[ColumnMetadataPayload] = Field(default_factory=_empty_column_metadata_payloads)
    confidence_delta: float | None = None

    def to_result(self, table: str) -> ColumnIntrospectionResult:
        """Return a :class:`ColumnIntrospectionResult` instance."""
        delta = self.confidence_delta
        if delta is None:
            delta = 0.0 if self.exists else NEGATIVE_RESULT_CONFIDENCE_DELTA
        metadata = tuple(column.to_metadata() for column in self.columns)
        return ColumnIntrospectionResult(
            table=table,
            schema=self.schema_name,
            exists=self.exists,
            columns=metadata,
            confidence_delta=delta,
        )


class DatabaseFixturePayload(BaseModel):
    """Fixture-based configuration for database verification."""

    model_config = ConfigDict(extra="forbid")

    tables: dict[str, TableFixturePayload] = Field(default_factory=dict)
    columns: dict[str, ColumnFixturePayload] = Field(default_factory=dict)


class AllowedDatabaseConfig(BaseModel):
    """Allowlisted database configuration the server trusts."""

    model_config = ConfigDict(extra="forbid")

    engine: str
    database: str | None = None
    dsn: str | None = None
    uri: bool = False

    def create_adapter(self) -> DatabaseAdapter:
        """Instantiate an adapter using the allowlisted configuration."""
        return create_database_adapter(
            self.engine,
            database=self.database,
            dsn=self.dsn,
            uri=self.uri,
        )


def _empty_allowed_database_configs() -> dict[str, AllowedDatabaseConfig]:
    return {}


class DatabaseOptions(BaseModel):
    """Options controlling database verification behaviour."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    fixture: DatabaseFixturePayload | None = None
    engine: str | None = None
    database: str | None = None
    dsn: str | None = None
    uri: bool = False
    schema_name: str | None = Field(default=None, alias="schema")
    preset: str | None = None

    def build(
        self,
        allowlist: Mapping[str, AllowedDatabaseConfig] | None = None,
    ) -> tuple[DatabaseAdapter, str | None]:
        """Return a database adapter and the requested schema namespace."""
        disallowed_fields = [
            name
            for name, value in (
                ("engine", self.engine),
                ("database", self.database),
                ("dsn", self.dsn),
                ("uri", True if self.uri else None),
            )
            if value is not None
        ]
        if disallowed_fields:
            formatted = ", ".join(disallowed_fields)
            message = (
                "Custom database configuration is not permitted for FastMCP "
                f"(received {formatted}). Use fixtures or an allowlisted preset"
            )
            raise ValueError(message)

        if self.fixture is not None and self.preset is not None:
            message = "Database fixture and preset cannot be combined"
            raise ValueError(message)

        if self.fixture is not None:
            adapter = _FixtureDatabaseAdapter(self.fixture)
            return adapter, self.schema_name

        if self.preset is not None:
            allowlist = allowlist or {}
            config = allowlist.get(self.preset)
            if config is None:
                message = f"Database preset {self.preset!r} is not permitted"
                raise ValueError(message)
            adapter = config.create_adapter()
            return adapter, self.schema_name

        adapter = _FixtureDatabaseAdapter(DatabaseFixturePayload())
        return adapter, self.schema_name


def _empty_spec_changes() -> list[SpecChange]:
    return []


class SpecImpactRequest(BaseModel):
    """Payload for the specification impact tool."""

    model_config = ConfigDict(extra="forbid")

    changes: list[SpecChange] = Field(default_factory=_empty_spec_changes)
    consumer_service: str | None = None
    provider_service: str | None = None
    base_confidence: float = 0.4


@dataclass
class MCPRuntime:
    """Stateful helpers shared between FastMCP tool invocations."""

    normalize_agent: NormalizeAgent
    database_allowlist: Mapping[str, AllowedDatabaseConfig] = field(
        default_factory=_empty_allowed_database_configs,
    )

    def normalize_crud(
        self,
        code_snippets: Sequence[CodeSnippetPayload],
        hints: NormalizationHintsPayload | None,
    ) -> CRUDActionList:
        """Normalize CRUD actions from the provided code snippets."""
        requests = [snippet.to_request() for snippet in code_snippets]
        default_service: str | None = None
        glossary: TermGlossary | None = None
        if hints is not None:
            default_service, glossary = hints.to_kwargs()
        return self.normalize_agent.normalize(
            requests,
            default_service=default_service,
            glossary_overrides=glossary,
        )

    def verify_crud(
        self,
        crud: CRUDActionList,
        repo_opts: RepoOptions | None,
        db_opts: DatabaseOptions | None,
    ) -> Graph:
        """Verify CRUD actions using repository and database evidence."""
        repository, base_query = self._build_repository(repo_opts)
        database, schema = self._build_database(db_opts)
        agent = VerifyAgent(repository=repository, database=database)
        return agent.verify(crud.actions, repo_base_query=base_query, schema=schema)

    def run_crud_pipeline(
        self,
        code_snippets: Sequence[CodeSnippetPayload],
        repo_opts: RepoOptions | None,
        db_opts: DatabaseOptions | None,
        hints: NormalizationHintsPayload | None,
    ) -> Graph:
        """Execute the end-to-end CRUD normalization pipeline."""
        repository, base_query = self._build_repository(repo_opts)
        database, schema = self._build_database(db_opts)
        orchestrator = CrudWorkflowOrchestrator(
            normalize_agent=self.normalize_agent,
            verify_agent=VerifyAgent(repository=repository, database=database),
        )
        pipeline = CrudNormalizationPipeline(orchestrator=orchestrator)
        requests = [snippet.to_request() for snippet in code_snippets]
        default_service: str | None = None
        glossary: TermGlossary | None = None
        if hints is not None:
            default_service, glossary = hints.to_kwargs()
        return pipeline.run(
            requests,
            default_service=default_service,
            glossary_overrides=glossary,
            repo_base_query=base_query,
            schema=schema,
        )

    def spec_impact(self, request: SpecImpactRequest) -> Graph:
        """Create an impact graph from API specification changes."""
        return build_spec_impact_graph(
            request.changes,
            consumer_service=request.consumer_service,
            provider_service=request.provider_service,
            base_confidence=request.base_confidence,
        )

    def merge_graphs(self, graphs: Sequence[Graph]) -> Graph:
        """Merge multiple graphs into a single consolidated view."""
        node_index: dict[str, Node] = {}
        edge_index: dict[tuple[str, str, str], Edge] = {}
        schema_version: str | None = None

        for graph in graphs:
            if schema_version is None and graph.schema_version:
                schema_version = graph.schema_version

            for node in graph.nodes:
                existing = node_index.get(node.id)
                if existing is None:
                    node_index[node.id] = node.model_copy(deep=True)
                else:
                    existing.attrs.update(node.attrs)

            for edge in graph.edges:
                key = (edge.from_id, edge.to_id, edge.type.value)
                existing_edge = edge_index.get(key)
                if existing_edge is None:
                    edge_index[key] = edge.model_copy(deep=True)
                    continue

                seen_evidence = {
                    (
                        ev.type,
                        ev.locator,
                        ev.snippet,
                        ev.source_tool,
                    )
                    for ev in existing_edge.evidence
                }
                for evidence in edge.evidence:
                    fingerprint = (
                        evidence.type,
                        evidence.locator,
                        evidence.snippet,
                        evidence.source_tool,
                    )
                    if fingerprint not in seen_evidence:
                        existing_edge.evidence.append(evidence.model_copy(deep=True))
                        seen_evidence.add(fingerprint)
                existing_edge.confidence = max(existing_edge.confidence, edge.confidence)

        nodes = sorted(node_index.values(), key=lambda item: item.id)
        edges = sorted(
            edge_index.values(),
            key=lambda item: (item.from_id, item.to_id, item.type.value, -item.confidence),
        )
        version = schema_version or Graph().schema_version
        return Graph(nodes=nodes, edges=edges, schema_version=version)

    def _build_repository(self, options: RepoOptions | None) -> tuple[RepositorySearcher, SearchQuery]:
        settings = options or RepoOptions()
        return settings.build()

    def _build_database(self, options: DatabaseOptions | None) -> tuple[DatabaseAdapter, str | None]:
        settings = options or DatabaseOptions()
        return settings.build(self.database_allowlist)


class _FixtureDatabaseAdapter(DatabaseAdapter):
    """Simple fixture-backed database adapter for deterministic responses."""

    def __init__(self, payload: DatabaseFixturePayload) -> None:
        self._tables: dict[tuple[str, str], TableIntrospectionResult] = {
            (details.schema_name or "", table): details.to_result(table)
            for table, details in payload.tables.items()
        }
        self._columns: dict[tuple[str, str], ColumnIntrospectionResult] = {
            (details.schema_name or "", table): details.to_result(table)
            for table, details in payload.columns.items()
        }

    def introspect_table(self, table: str, *, schema: str | None = None) -> TableIntrospectionResult:
        key = (schema or "", table)
        result = self._tables.get(key)
        if result is not None:
            return result
        return TableIntrospectionResult(
            table=table,
            schema=schema if schema else None,
            exists=False,
            row_count=None,
            confidence_delta=NEGATIVE_RESULT_CONFIDENCE_DELTA,
        )

    def introspect_columns(self, table: str, *, schema: str | None = None) -> ColumnIntrospectionResult:
        key = (schema or "", table)
        result = self._columns.get(key)
        if result is not None:
            return result
        return ColumnIntrospectionResult(
            table=table,
            schema=schema if schema else None,
            exists=False,
            columns=(),
            confidence_delta=NEGATIVE_RESULT_CONFIDENCE_DELTA,
        )

    def explain(self, sql: str) -> ExplainPlanResult:  # pragma: no cover - defensive guard
        del sql
        message = "EXPLAIN is not supported for fixture-backed adapters"
        raise DatabaseToolError(message)


class MCPExposure:
    """FastMCP(stdio) exposure that publishes yuragi tools."""

    def __init__(self) -> None:
        """Initialise the FastMCP server and register all tools."""
        self._mcp = FastMCP("yuragi")
        self._runtime: MCPRuntime | None = None

        @self._mcp.tool()
        def yuragi_normalize_crud(
            code_snippets: list[CodeSnippetPayload],
            hints: NormalizationHintsPayload | None = None,
        ) -> CRUDActionList:
            runtime = self._require_runtime()
            return runtime.normalize_crud(code_snippets, hints)

        @self._mcp.tool()
        def yuragi_verify_crud(
            crud: CRUDActionList,
            repo_opts: RepoOptions | None = None,
            db_opts: DatabaseOptions | None = None,
        ) -> Graph:
            runtime = self._require_runtime()
            return runtime.verify_crud(crud, repo_opts, db_opts)

        @self._mcp.tool()
        def yuragi_run_crud_pipeline(
            code_snippets: list[CodeSnippetPayload],
            repo_opts: RepoOptions | None = None,
            db_opts: DatabaseOptions | None = None,
            hints: NormalizationHintsPayload | None = None,
        ) -> Graph:
            runtime = self._require_runtime()
            return runtime.run_crud_pipeline(code_snippets, repo_opts, db_opts, hints)

        @self._mcp.tool()
        def yuragi_spec_impact(
            request: SpecImpactRequest,
        ) -> Graph:
            runtime = self._require_runtime()
            return runtime.spec_impact(request)

        @self._mcp.tool()
        def yuragi_merge_graphs(
            graphs: list[Graph],
        ) -> Graph:
            runtime = self._require_runtime()
            return runtime.merge_graphs(graphs)

        self._normalize_tool = yuragi_normalize_crud
        self._verify_tool = yuragi_verify_crud
        self._pipeline_tool = yuragi_run_crud_pipeline
        self._spec_tool = yuragi_spec_impact
        self._merge_tool = yuragi_merge_graphs

    def serve(self, *, config: Mapping[str, Any] | None = None) -> None:
        """Start the FastMCP server and publish the yuragi tools."""
        allowlist: dict[str, AllowedDatabaseConfig] = {}
        if config is not None:
            raw_allowlist = config.get("database_allowlist")
            if raw_allowlist is not None:
                if not isinstance(raw_allowlist, Mapping):
                    message = "database_allowlist must be a mapping of preset names to configurations"
                    raise ValueError(message)
                allowlist = {
                    str(name): AllowedDatabaseConfig.model_validate(value)
                    for name, value in cast("Mapping[str, Any]", raw_allowlist).items()
                }

        self._runtime = MCPRuntime(
            normalize_agent=NormalizeAgent(),
            database_allowlist=allowlist,
        )
        try:
            strict_validation = True
            show_banner = True
            transport: Any | None = None
            transport_kwargs: dict[str, Any] = {}

            if config is not None:
                strict_validation = bool(config.get("strict_input_validation", True))
                show_banner = bool(config.get("show_banner", True))
                if "transport" in config:
                    transport = config.get("transport")
                raw_transport_kwargs = config.get("transport_kwargs")
                if isinstance(raw_transport_kwargs, Mapping):
                    transport_kwargs = dict(cast("Mapping[str, Any]", raw_transport_kwargs))

            self._mcp.strict_input_validation = strict_validation
            self._mcp.run(transport=transport, show_banner=show_banner, **transport_kwargs)
        finally:
            self._runtime = None

    def _require_runtime(self) -> MCPRuntime:
        runtime = self._runtime
        if runtime is None:  # pragma: no cover - defensive guard
            message = "FastMCP runtime has not been initialised"
            raise RuntimeError(message)
        return runtime


__all__ = ["MCPExposure"]
