"""Tests for the database inspection adapters."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from yuragi.tools import (
    DatabaseToolError,
    NEGATIVE_RESULT_CONFIDENCE_DELTA,
    SQLiteDatabaseAdapter,
    create_database_adapter,
)

if TYPE_CHECKING:
    from pathlib import Path


def _initialize_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE sample (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                notes TEXT
            );
            INSERT INTO sample (id, name, notes) VALUES (1, 'alpha', 'primary');
            INSERT INTO sample (id, name, notes) VALUES (2, 'beta', NULL);
            """,
        )
    finally:
        connection.close()


def test_sqlite_introspect_table_reports_existence(tmp_path: Path) -> None:
    """SQLite adapter should confirm table existence for a known table."""
    database_path = tmp_path / "example.db"
    _initialize_database(database_path)
    adapter = SQLiteDatabaseAdapter(database_path)

    result = adapter.introspect_table("sample")

    assert result.exists is True
    assert result.row_count is None
    assert result.confidence_delta == 0.0


def test_sqlite_introspect_columns_returns_metadata(tmp_path: Path) -> None:
    """SQLite adapter should expose column metadata including nullability."""
    database_path = tmp_path / "example.db"
    _initialize_database(database_path)
    adapter = SQLiteDatabaseAdapter(database_path)

    result = adapter.introspect_columns("sample")

    assert result.exists is True
    column_map = {column.name: column for column in result.columns}
    assert column_map["id"].data_type.upper() == "INTEGER"
    assert column_map["id"].nullable is False
    assert column_map["name"].nullable is False
    assert column_map["notes"].nullable is True


def test_sqlite_missing_table_applies_negative_confidence(tmp_path: Path) -> None:
    """Missing tables should reduce the confidence contribution."""
    database_path = tmp_path / "example.db"
    _initialize_database(database_path)
    adapter = SQLiteDatabaseAdapter(database_path)

    result = adapter.introspect_table("missing")

    assert result.exists is False
    assert result.row_count is None
    assert result.confidence_delta == NEGATIVE_RESULT_CONFIDENCE_DELTA


def test_sqlite_explain_returns_plan_steps(tmp_path: Path) -> None:
    """Explain plans should include textual steps describing the query."""
    database_path = tmp_path / "example.db"
    _initialize_database(database_path)
    adapter = SQLiteDatabaseAdapter(database_path)

    plan = adapter.explain("SELECT name FROM sample WHERE id = 1")

    assert plan.sql == "SELECT name FROM sample WHERE id = 1"
    assert plan.plan
    assert all(isinstance(step, str) and step for step in plan.plan)


def test_sqlite_explain_rejects_multiple_statements(tmp_path: Path) -> None:
    """Explain should reject multiple SQL statements for safety."""
    database_path = tmp_path / "example.db"
    _initialize_database(database_path)
    adapter = SQLiteDatabaseAdapter(database_path)

    with pytest.raises(DatabaseToolError):
        adapter.explain("SELECT 1; SELECT 2")


def test_factory_creates_sqlite_adapter(tmp_path: Path) -> None:
    """Factory should return a SQLite adapter when requested."""
    database_path = tmp_path / "example.db"
    _initialize_database(database_path)

    adapter = create_database_adapter("sqlite", database=database_path)

    assert isinstance(adapter, SQLiteDatabaseAdapter)


def test_factory_rejects_unknown_engine() -> None:
    """Factory should reject unsupported engines with a clear error."""
    with pytest.raises(ValueError, match="Unsupported database engine"):
        create_database_adapter("oracle", database="dummy")
