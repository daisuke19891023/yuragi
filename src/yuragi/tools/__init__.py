"""Adapter utilities for repository tooling."""

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
    "CLIAdapter",
    "CallableAdapter",
    "HTTPAdapter",
    "RepoAdapterError",
    "RepoHit",
    "RepositorySearcher",
    "SearchQuery",
]
