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
    "TableIntrospectionResult",
    "create_database_adapter",
]
