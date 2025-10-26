"""Tests for the exposure factory."""

from __future__ import annotations

import pytest

from yuragi.interfases.cli.app import CLIExposure
from yuragi.interfases.factory import (
    make_exposure,
    resolve_exposure_from_environment,
)
from yuragi.interfases.mcp.server_fastmcp import MCPExposure


@pytest.mark.parametrize(
    "kind", ["cli", "mcp"],
)
def test_make_exposure_known_kinds(kind: str) -> None:
    """Known exposure kinds return the expected instance type."""
    exposure = make_exposure(kind)

    expected_type = CLIExposure if kind == "cli" else MCPExposure
    assert isinstance(exposure, expected_type)


def test_make_exposure_unknown_kind() -> None:
    """Unknown exposure kinds raise a ValueError."""
    with pytest.raises(ValueError, match="unknown exposure kind: unknown"):
        make_exposure("unknown")


def test_resolve_exposure_defaults_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment resolver falls back to the CLI exposure."""
    monkeypatch.delenv("YURAGI_EXPOSE", raising=False)

    exposure = resolve_exposure_from_environment()

    assert isinstance(exposure, CLIExposure)


def test_resolve_exposure_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolver honours the environment variable override."""
    monkeypatch.setenv("YURAGI_EXPOSE", "mcp")

    exposure = resolve_exposure_from_environment()

    assert isinstance(exposure, MCPExposure)


def test_resolve_exposure_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid environment values propagate as ValueError."""
    monkeypatch.setenv("YURAGI_EXPOSE", "invalid")

    with pytest.raises(ValueError, match="unknown exposure kind: invalid"):
        resolve_exposure_from_environment()
