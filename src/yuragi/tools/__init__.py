"""Adapter utilities for repository tooling."""

from .db import (
    ColumnIntrospectionResult,
    ColumnMetadata,
    DatabaseAdapter,
    DatabaseToolError,
    ExplainPlanResult,
    NEGATIVE_RESULT_CONFIDENCE_DELTA,
    PostgresDatabaseAdapter,
    SQLiteDatabaseAdapter,
    TableIntrospectionResult,
    create_database_adapter,
)
from .repo import (
    CLIAdapter,
    CallableAdapter,
    HTTPAdapter,
    RepoAdapterError,
    RepoHit,
    RepositorySearcher,
    SearchQuery,
)
from .specs import (
    SpecChange,
    build_spec_impact_graph,
    parse_buf_breaking,
    parse_graphql_inspector,
    parse_oasdiff,
)

__all__ = [
    "NEGATIVE_RESULT_CONFIDENCE_DELTA",
    "CLIAdapter",
    "CallableAdapter",
    "ColumnIntrospectionResult",
    "ColumnMetadata",
    "DatabaseAdapter",
    "DatabaseToolError",
    "ExplainPlanResult",
    "HTTPAdapter",
    "PostgresDatabaseAdapter",
    "RepoAdapterError",
    "RepoHit",
    "RepositorySearcher",
    "SQLiteDatabaseAdapter",
    "SearchQuery",
    "SpecChange",
    "TableIntrospectionResult",
    "build_spec_impact_graph",
    "create_database_adapter",
    "parse_buf_breaking",
    "parse_graphql_inspector",
    "parse_oasdiff",
]
