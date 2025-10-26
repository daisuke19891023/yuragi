"""Tests for the FastMCP server exposure."""

from __future__ import annotations

import pytest

from yuragi.core.errors import ExposureConfigurationError

from yuragi.interfases.mcp.server_fastmcp import (
    AllowedDatabaseConfig,
    DatabaseFixturePayload,
    DatabaseOptions,
)
from yuragi.tools.db import SQLiteDatabaseAdapter


def test_database_options_rejects_client_engine_configuration() -> None:
    """Custom engine parameters from the client should be rejected."""
    options = DatabaseOptions(engine="sqlite", database=":memory:")

    with pytest.raises(ExposureConfigurationError, match="Custom database configuration is not permitted"):
        options.build()


def test_database_options_rejects_client_dsn_configuration() -> None:
    """DSN overrides are not allowed in FastMCP requests."""
    options = DatabaseOptions(dsn="postgresql://localhost/db")

    with pytest.raises(ExposureConfigurationError, match="Custom database configuration is not permitted"):
        options.build()


def test_database_options_rejects_unknown_preset() -> None:
    """Preset lookups must be part of the server allowlist."""
    options = DatabaseOptions(preset="production")

    with pytest.raises(ExposureConfigurationError, match="preset 'production' is not permitted"):
        options.build({})


def test_database_options_uses_allowlisted_preset() -> None:
    """Allowlisted presets should create adapters using server-provided settings."""
    options = DatabaseOptions(preset="local")
    allowlist = {
        "local": AllowedDatabaseConfig(engine="sqlite", database=":memory:"),
    }

    adapter, schema = options.build(allowlist)

    assert isinstance(adapter, SQLiteDatabaseAdapter)
    assert schema is None


def test_database_options_returns_fixture_adapter() -> None:
    """Fixture-backed adapters remain available."""
    fixture = DatabaseFixturePayload()
    options = DatabaseOptions(fixture=fixture)

    adapter, schema = options.build()

    assert schema is None
    assert adapter.introspect_table("nonexistent").exists is False
