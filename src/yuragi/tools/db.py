"""Database inspection utilities used during verification."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
import os
import re
from sqlite3 import Connection as SQLiteConnection
from sqlite3 import connect as sqlite_connect
from typing import Any, Protocol, runtime_checkable


NEGATIVE_RESULT_CONFIDENCE_DELTA = -0.3
"""Penalty applied when a database check yields a negative result."""


class DatabaseToolError(RuntimeError):
    """Raised when the database tool fails to execute an operation."""


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ensure_safe_identifier(identifier: str) -> str:
    """Validate that an identifier only contains safe characters."""
    if not _IDENTIFIER_PATTERN.fullmatch(identifier):
        error_message = f"Unsafe identifier: {identifier!r}"
        raise DatabaseToolError(error_message)
    return identifier


def _quote_identifier(identifier: str) -> str:
    """Return a quoted version of the identifier."""
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def _validate_explain_sql(sql: str) -> str:
    """Ensure the SQL used for EXPLAIN is a single statement."""
    stripped = sql.strip()
    if not stripped:
        error_message = "SQL statement for explain must not be empty"
        raise DatabaseToolError(error_message)
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    if ";" in stripped:
        error_message = "SQL statement must not contain multiple statements"
        raise DatabaseToolError(error_message)
    return stripped


@dataclass(frozen=True)
class ColumnMetadata:
    """Metadata describing a database column."""

    name: str
    data_type: str
    nullable: bool
    default: str | None


@dataclass(frozen=True)
class TableIntrospectionResult:
    """Outcome for a table existence check."""

    table: str
    schema: str | None
    exists: bool
    row_count: int | None = None
    confidence_delta: float = 0.0


@dataclass(frozen=True)
class ColumnIntrospectionResult:
    """Outcome for a column inspection."""

    table: str
    schema: str | None
    exists: bool
    columns: tuple[ColumnMetadata, ...]
    confidence_delta: float = 0.0


@dataclass(frozen=True)
class ExplainPlanResult:
    """Explain plan for a SQL statement."""

    sql: str
    plan: tuple[str, ...]


@runtime_checkable
class DatabaseAdapter(Protocol):
    """Protocol implemented by database adapters."""

    def introspect_table(
        self,
        table: str,
        *,
        schema: str | None = None,
    ) -> TableIntrospectionResult:
        """Return metadata about a table."""
        ...

    def introspect_columns(
        self,
        table: str,
        *,
        schema: str | None = None,
    ) -> ColumnIntrospectionResult:
        """Return column metadata for a table."""
        ...

    def explain(self, sql: str) -> ExplainPlanResult:
        """Return the execution plan for a SQL statement."""
        ...


class SQLiteDatabaseAdapter:
    """Database adapter backed by SQLite."""

    def __init__(self, database: str | os.PathLike[str], *, uri: bool = False) -> None:
        """Create a new adapter bound to the provided SQLite database."""
        self._database = os.fspath(database)
        self._uri = uri

    def _connect(self) -> SQLiteConnection:
        """Return a new SQLite connection with default configuration."""
        connection = sqlite_connect(self._database, uri=self._uri)
        connection.row_factory = None
        return connection

    def introspect_table(
        self,
        table: str,
        *,
        schema: str | None = None,
    ) -> TableIntrospectionResult:
        """Report whether a table exists and return basic statistics."""
        del schema
        safe_table = _ensure_safe_identifier(table)
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                (safe_table,),
            )
            row = cursor.fetchone()
            exists = row is not None
        row_count: int | None = None
        confidence_delta = 0.0 if exists else NEGATIVE_RESULT_CONFIDENCE_DELTA
        return TableIntrospectionResult(safe_table, None, exists, row_count, confidence_delta)

    def introspect_columns(
        self,
        table: str,
        *,
        schema: str | None = None,
    ) -> ColumnIntrospectionResult:
        """Return metadata describing the columns for a table."""
        del schema
        safe_table = _ensure_safe_identifier(table)
        quoted_table = _quote_identifier(safe_table)
        with closing(self._connect()) as connection:
            pragma_statement = "PRAGMA table_info(" + quoted_table + ")"
            cursor = connection.execute(pragma_statement)
            rows = cursor.fetchall()
        exists = bool(rows)
        columns: tuple[ColumnMetadata, ...] = tuple(
            ColumnMetadata(
                name=str(row[1]),
                data_type=str(row[2]),
                nullable=(row[3] == 0 and int(row[5]) == 0),
                default=str(row[4]) if row[4] is not None else None,
            )
            for row in rows
        )
        confidence_delta = 0.0 if exists else NEGATIVE_RESULT_CONFIDENCE_DELTA
        return ColumnIntrospectionResult(
            safe_table,
            None,
            exists,
            columns,
            confidence_delta,
        )

    def explain(self, sql: str) -> ExplainPlanResult:
        """Return the query plan steps for the supplied SQL statement."""
        validated_sql = _validate_explain_sql(sql)
        with closing(self._connect()) as connection:
            explain_statement = "EXPLAIN QUERY PLAN " + validated_sql
            cursor = connection.execute(explain_statement)
            rows = cursor.fetchall()
        plan = tuple(str(row[3]) for row in rows)
        return ExplainPlanResult(validated_sql, plan)


class PostgresDatabaseAdapter:
    """Database adapter backed by PostgreSQL."""

    def __init__(self, dsn: str, **connect_kwargs: object) -> None:
        """Create a new adapter using the provided DSN."""
        if find_spec("psycopg") is None:
            error_message = (
                "psycopg is required for the PostgreSQL adapter but is not installed"
            )
            raise ModuleNotFoundError(error_message)
        self._dsn = dsn
        self._connect_kwargs = connect_kwargs
        self._psycopg = import_module("psycopg")
        self._sql = import_module("psycopg.sql")

    def _connect(self) -> Any:
        """Return a new PostgreSQL connection."""
        return self._psycopg.connect(self._dsn, **self._connect_kwargs)

    def introspect_table(
        self,
        table: str,
        *,
        schema: str | None = None,
    ) -> TableIntrospectionResult:
        """Report whether a table exists within the selected schema."""
        schema_filter = _ensure_safe_identifier(schema) if schema else "public"
        schema_filter = _ensure_safe_identifier(schema_filter)
        safe_table = _ensure_safe_identifier(table)
        exists_query = (
            "SELECT COUNT(1) FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s"
        )
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                sql_module = self._sql
                cursor.execute(exists_query, (schema_filter, safe_table))
                exists = bool(cursor.fetchone()[0])
                row_count: int | None = None
                if exists:
                    count_query = sql_module.SQL("SELECT COUNT(1) FROM {}.{}").format(
                        sql_module.Identifier(schema_filter),
                        sql_module.Identifier(safe_table),
                    )
                    cursor.execute(count_query)
                    count_row = cursor.fetchone()
                    row_count = int(count_row[0]) if count_row is not None else 0
        confidence_delta = 0.0 if exists else NEGATIVE_RESULT_CONFIDENCE_DELTA
        return TableIntrospectionResult(
            safe_table,
            schema_filter,
            exists,
            row_count,
            confidence_delta,
        )

    def introspect_columns(
        self,
        table: str,
        *,
        schema: str | None = None,
    ) -> ColumnIntrospectionResult:
        """Return metadata describing the columns for a PostgreSQL table."""
        schema_filter = _ensure_safe_identifier(schema) if schema else "public"
        schema_filter = _ensure_safe_identifier(schema_filter)
        safe_table = _ensure_safe_identifier(table)
        query = (
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position"
        )
        with closing(self._connect()) as connection, connection.cursor() as cursor:
            cursor.execute(query, (schema_filter, safe_table))
            rows = cursor.fetchall()
        exists = bool(rows)
        columns: tuple[ColumnMetadata, ...] = tuple(
            ColumnMetadata(
                name=str(row[0]),
                data_type=str(row[1]),
                nullable=row[2] == "YES",
                default=str(row[3]) if row[3] is not None else None,
            )
            for row in rows
        )
        confidence_delta = 0.0 if exists else NEGATIVE_RESULT_CONFIDENCE_DELTA
        return ColumnIntrospectionResult(
            safe_table,
            schema_filter,
            exists,
            columns,
            confidence_delta,
        )

    def explain(self, sql: str) -> ExplainPlanResult:
        """Return the textual EXPLAIN plan for the supplied SQL."""
        validated_sql = _validate_explain_sql(sql)
        sql_module = self._sql
        explain_query = sql_module.SQL("EXPLAIN (FORMAT TEXT) {}" ).format(
            sql_module.SQL(validated_sql),
        )
        with closing(self._connect()) as connection, connection.cursor() as cursor:
            cursor.execute(explain_query)
            rows = cursor.fetchall()
        plan = tuple(str(row[0]) for row in rows)
        return ExplainPlanResult(validated_sql, plan)


def create_database_adapter(
    engine: str,
    *,
    database: str | os.PathLike[str] | None = None,
    dsn: str | None = None,
    uri: bool = False,
    **kwargs: object,
) -> DatabaseAdapter:
    """Return a database adapter for the requested engine."""
    normalized_engine = engine.lower()
    if normalized_engine in {"sqlite", "sqlite3"}:
        if database is None:
            error_message = "SQLite adapter requires a database path"
            raise ValueError(error_message)
        return SQLiteDatabaseAdapter(database, uri=uri)
    if normalized_engine in {"postgres", "postgresql", "psql"}:
        if dsn is None:
            error_message = "PostgreSQL adapter requires a DSN"
            raise ValueError(error_message)
        return PostgresDatabaseAdapter(dsn, **kwargs)
    error_message = f"Unsupported database engine: {engine}"
    raise ValueError(error_message)


__all__ = [
    "NEGATIVE_RESULT_CONFIDENCE_DELTA",
    "ColumnIntrospectionResult",
    "ColumnMetadata",
    "DatabaseAdapter",
    "DatabaseToolError",
    "ExplainPlanResult",
    "PostgresDatabaseAdapter",
    "SQLiteDatabaseAdapter",
    "TableIntrospectionResult",
    "create_database_adapter",
]
