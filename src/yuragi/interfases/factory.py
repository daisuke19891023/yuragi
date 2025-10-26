"""Factories for creating exposure entry points."""

from __future__ import annotations

import os
import typing
from typing import TYPE_CHECKING

from yuragi.interfases.cli.app import CLIExposure
from yuragi.interfases.mcp.server_fastmcp import MCPExposure

if TYPE_CHECKING:
    from yuragi.interfases.types import Exposure

_EXPOSURE_FACTORIES = {
    "cli": CLIExposure,
    "mcp": MCPExposure,
}


def make_exposure(kind: str) -> Exposure:
    """Create an exposure implementation for *kind*."""
    try:
        factory = _EXPOSURE_FACTORIES[kind]
    except KeyError:
        message = f"unknown exposure kind: {kind}"
        raise ValueError(message) from None
    return factory()


def resolve_exposure_from_environment(
    env: typing.Mapping[str, str] | None = None,
    *,
    default: str = "cli",
) -> Exposure:
    """Resolve the exposure based on the provided environment mapping."""
    environment = os.environ if env is None else env
    kind = environment.get("YURAGI_EXPOSE", default)
    return make_exposure(kind)


__all__ = ["make_exposure", "resolve_exposure_from_environment"]
