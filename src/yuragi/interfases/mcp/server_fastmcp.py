"""Stub FastMCP exposure for the yuragi project."""

from __future__ import annotations

from typing import Any
import typing


class MCPExposure:
    """Placeholder exposure that will host the FastMCP server."""

    def serve(self, *, config: typing.Mapping[str, Any] | None = None) -> None:
        """Start the FastMCP server (not yet implemented)."""
        message = "FastMCP exposure is not implemented yet."
        raise NotImplementedError(message)


__all__ = ["MCPExposure"]
