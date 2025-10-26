"""Tests for the repository adapters."""

from __future__ import annotations

import json
from subprocess import CompletedProcess
from typing import Any, cast

import httpx
import pytest

from yuragi.tools.repo import (
    CLIAdapter,
    CallableAdapter,
    HTTPAdapter,
    RepoAdapterError,
    RepoHit,
    RepositorySearcher,
    SearchQuery,
)


def test_cli_adapter_normalizes_ripgrep_output() -> None:
    """The CLI adapter should parse ripgrep JSON into RepoHit objects."""
    captured_args: list[list[str]] = []

    stdout_lines = [
        json.dumps({"type": "begin", "data": {"path": {"text": "src/app.py"}}}),
        json.dumps(
            {
                "type": "context",
                "data": {"lines": {"text": "before match\n"}, "line_number": 41},
            },
        ),
        json.dumps(
            {
                "type": "match",
                "data": {
                    "path": {"text": "src/app.py"},
                    "line_number": 42,
                    "lines": {"text": "actual match\n"},
                    "submatches": [
                        {
                            "match": {"text": "actual"},
                            "start": 0,
                            "end": 6,
                        },
                    ],
                },
            },
        ),
    ]

    def fake_run(args: list[str], **_: Any) -> CompletedProcess[str]:
        captured_args.append(list(args))
        return CompletedProcess(
            args=args,
            returncode=0,
            stdout="\n".join(stdout_lines),
            stderr="",
        )

    adapter = CLIAdapter(["rg", "--json"], allowed_commands={"rg"}, runner=fake_run)
    query = SearchQuery(pattern="actual", paths=("src",), context_lines=1)

    hits = adapter.search(query)

    assert captured_args == [["rg", "--json", "actual", "src"]]
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, RepoHit)
    assert hit.path == "src/app.py"
    assert hit.line_number == 42
    assert hit.line == "actual match"
    assert hit.context_before == ["before match"]
    submatches_raw = hit.metadata.get("submatches")
    assert isinstance(submatches_raw, list)
    first_submatch = submatches_raw[0]
    assert isinstance(first_submatch, dict)
    match_payload = first_submatch.get("match", {})
    assert isinstance(match_payload, dict)
    assert match_payload.get("text") == "actual"


def test_cli_adapter_empty_output() -> None:
    """A CLI command that returns no output should produce no hits."""

    def fake_runner(*_: Any, **__: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    adapter = CLIAdapter(["rg", "--json"], allowed_commands={"rg"}, runner=fake_runner)
    hits = adapter.search(SearchQuery(pattern="missing"))

    assert hits == []


def test_http_adapter_uses_client_request() -> None:
    """HTTP adapter should send payload and normalize response hits."""
    captured_payload: dict[str, Any] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        parsed = cast("dict[str, Any]", json.loads(request.content.decode()))
        captured_payload = parsed
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "path": "src/app.py",
                        "line_number": 7,
                        "line": "value",
                        "context_after": ["next"],
                        "score": 0.5,
                    },
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        adapter = HTTPAdapter(
            client,
            url="https://example.test/search",
            method="post",
            headers={"X-Test": "1"},
        )
        query = SearchQuery(
            pattern="value",
            paths=("src",),
            flags=("--iglob", "*.py"),
            context_lines=2,
        )
        hits = adapter.search(query)

    assert captured_payload is not None
    assert captured_payload == {
        "query": "value",
        "paths": ["src"],
        "flags": ["--iglob", "*.py"],
        "context_lines": 2,
    }
    assert len(hits) == 1
    hit = hits[0]
    assert hit.path == "src/app.py"
    assert hit.context_after == ["next"]
    assert hit.score == 0.5


def test_callable_adapter_and_repository_searcher_retry() -> None:
    """Callable adapter integrates with the searcher to retry candidates."""
    observed_patterns: list[str] = []

    def fake_search(query: SearchQuery) -> list[dict[str, Any]]:
        observed_patterns.append(query.pattern)
        if query.pattern != "actual":
            return []
        return [
            {
                "path": "src/app.py",
                "line_number": 5,
                "line": "actual value",
            },
        ]

    adapter = CallableAdapter(fake_search)
    searcher = RepositorySearcher(adapter)
    base_query = SearchQuery(pattern="placeholder", paths=("src",))

    hits = searcher.search_candidates(["ambiguous", "actual"], base_query=base_query)

    assert observed_patterns == ["ambiguous", "actual"]
    assert len(hits) == 1
    assert hits[0].line == "actual value"


def test_normalize_hit_rejects_invalid_payload() -> None:
    """Adapters should raise a helpful error when a hit payload is invalid."""
    adapter = CallableAdapter(lambda _: ["not a mapping"])
    with pytest.raises(RepoAdapterError):
        adapter.search(SearchQuery(pattern="oops"))
