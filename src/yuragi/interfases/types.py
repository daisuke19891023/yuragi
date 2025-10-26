"""Shared interface definitions for yuragi exposures."""

from __future__ import annotations

from typing import Any, Protocol
import typing


class Exposure(Protocol):
    """Protocol describing an executable exposure entry point."""

    def serve(self, *, config: typing.Mapping[str, Any] | None = None) -> None:
        """Start the exposure using an optional configuration mapping."""
        raise NotImplementedError


__all__ = ["Exposure"]
