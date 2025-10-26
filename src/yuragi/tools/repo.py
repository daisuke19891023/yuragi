"""Repository search adapters used by verification tooling."""

from __future__ import annotations

import json
import os
from collections import deque
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable, cast

import httpx
from pydantic import BaseModel, Field, ValidationError

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

if TYPE_CHECKING:
    from subprocess import CompletedProcess

Runner = Callable[..., "CompletedProcess[str]"]

class RepoAdapterError(RuntimeError):
    """Raised when a repository adapter fails to perform a search."""

@dataclass(frozen=True)
class SearchQuery:
    """Search parameters shared across repository adapters."""

    pattern: str
    paths: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    context_lines: int = 0

    def with_pattern(self, pattern: str) -> SearchQuery:
        """Return a new query with the supplied search pattern."""
        return replace(self, pattern=pattern)


class RepoHit(BaseModel):
    """A normalized hit returned by a repository search adapter."""

    path: str
    line_number: int
    line: str
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)
    score: float | None = None
    metadata: dict[str, JSONValue] = Field(default_factory=dict)


@runtime_checkable
class SearchAdapter(Protocol):
    """Protocol implemented by repository search adapters."""

    def search(self, query: SearchQuery) -> list[RepoHit]:
        """Execute a search and return normalized hits."""
        ...


class CLIAdapter:
    """Adapter that executes an external command-line tool (e.g. ripgrep)."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        allowed_commands: Collection[str],
        runner: Runner,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Configure the adapter and validate the permitted executable."""
        if not command:
            error_message = "CLIAdapter requires a non-empty command sequence"
            raise ValueError(error_message)
        if not allowed_commands:
            error_message = "CLIAdapter requires at least one allowed command"
            raise ValueError(error_message)

        executable = command[0]
        executable_name = Path(executable).name
        if executable not in allowed_commands and executable_name not in allowed_commands:
            error_message = (
                "Executable is not present in the allowlist: "
                f"{executable}"
            )
            raise ValueError(error_message)

        if runner is None:
            error_message = "CLIAdapter requires a runner callable"
            raise ValueError(error_message)

        self._command = list(command)
        self._runner: Runner = runner
        self._env = os.environ.copy()
        if env:
            self._env.update(env)
        self._cwd = cwd
        self._timeout = timeout

    def search(self, query: SearchQuery) -> list[RepoHit]:
        """Run the CLI tool and normalize its JSON output."""
        args: list[str] = [*self._command]
        if query.flags:
            args.extend(query.flags)
        args.append(query.pattern)
        if query.paths:
            args.extend(query.paths)

        try:
            completed = self._runner(
                args,
                capture_output=True,
                check=False,
                cwd=self._cwd,
                env=self._env,
                text=True,
                timeout=self._timeout,
                shell=False,
            )
        except OSError as error:  # pragma: no cover - defensive guard
            error_message = "CLI execution failed"
            raise RepoAdapterError(error_message) from error

        if completed.returncode not in (0, 1):
            error_message = (
                f"CLI command {args!r} exited with status "
                f"{completed.returncode}: {completed.stderr.strip()}"
            )
            raise RepoAdapterError(error_message)

        if not completed.stdout.strip():
            return []

        return self._parse_stdout(completed.stdout, query.context_lines)

    @staticmethod
    def _parse_stdout(stdout: str, context_lines: int) -> list[RepoHit]:
        """Convert JSON lines emitted by ripgrep into repo hits."""
        hits: list[RepoHit] = []
        maxlen = context_lines if context_lines > 0 else None
        context_buffer: deque[str] = deque(maxlen=maxlen)
        current_path: str | None = None
        for line in stdout.splitlines():
            event = CLIAdapter._load_event(line)
            if event is None:
                continue
            event_type, raw_data = event
            data: dict[str, Any] = dict(raw_data)
            if event_type == "begin":
                current_path = CLIAdapter._extract_path(data)
                context_buffer.clear()
                continue
            if event_type == "context":
                CLIAdapter._append_context_line(data, context_buffer, context_lines)
                continue
            if event_type != "match":
                continue

            hit = CLIAdapter._build_hit(data, current_path, list(context_buffer))
            if hit is not None:
                hits.append(hit)
            context_buffer.clear()
        return hits

    @staticmethod
    def _load_event(raw_line: str) -> tuple[str, Mapping[str, Any]] | None:
        """Return the ripgrep event tuple if the line is valid JSON."""
        stripped_line = raw_line.strip()
        if not stripped_line:
            return None
        try:
            payload = json.loads(stripped_line)
        except json.JSONDecodeError:
            return None

        if not isinstance(payload, dict):
            return None

        payload_dict = cast("dict[str, Any]", payload)
        event_type = payload_dict.get("type")
        data_obj = payload_dict.get("data")
        if not isinstance(event_type, str) or not isinstance(data_obj, dict):
            return None
        return event_type, cast("dict[str, Any]", data_obj)

    @staticmethod
    def _build_hit(
        data: Mapping[str, Any],
        current_path: str | None,
        context_before: list[str],
    ) -> RepoHit | None:
        """Create a :class:`RepoHit` for a ripgrep match event."""
        path: str | None = current_path
        path_entry = data.get("path")
        if isinstance(path_entry, Mapping):
            path_mapping = cast("Mapping[str, Any]", path_entry)
            path_value = path_mapping.get("text")
            if isinstance(path_value, str):
                path = path_value
        line_number = data.get("line_number")
        if path is None or line_number is None:
            return None
        lines_entry = data.get("lines")
        line_text = ""
        if isinstance(lines_entry, Mapping):
            line_mapping = cast("Mapping[str, Any]", lines_entry)
            text_value = line_mapping.get("text", "")
            if isinstance(text_value, str):
                line_text = text_value

        metadata: dict[str, JSONValue] = {}
        submatches = data.get("submatches")
        if submatches:
            metadata["submatches"] = submatches

        return RepoHit(
            path=path,
            line_number=int(line_number),
            line=line_text.rstrip("\n"),
            context_before=context_before,
            metadata=metadata,
        )

    @staticmethod
    def _extract_path(data: Mapping[str, Any]) -> str | None:
        """Extract a path string from a ripgrep event payload."""
        path_entry = data.get("path")
        if isinstance(path_entry, Mapping):
            path_mapping = cast("Mapping[str, Any]", path_entry)
            path_value = path_mapping.get("text")
            if isinstance(path_value, str):
                return path_value
        return None

    @staticmethod
    def _append_context_line(
        data: Mapping[str, Any],
        buffer: deque[str],
        context_lines: int,
    ) -> None:
        """Append a context line to the buffer when enabled."""
        if context_lines <= 0:
            return
        lines_entry = data.get("lines")
        if isinstance(lines_entry, Mapping):
            line_mapping = cast("Mapping[str, Any]", lines_entry)
            line_text = line_mapping.get("text", "")
            if isinstance(line_text, str):
                buffer.append(line_text.rstrip("\n"))


class HTTPAdapter:
    """Adapter that uses an HTTP endpoint to perform repository searches."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        url: str,
        method: str = "POST",
        timeout: float = 10.0,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Configure the HTTP transport used for repository searches."""
        self._client = client
        self._url = url
        self._method = method.upper()
        self._timeout = timeout
        self._headers = dict(headers or {})

    def search(self, query: SearchQuery) -> list[RepoHit]:
        """Send a request to the remote service and normalize hits."""
        payload: dict[str, Any] = {"query": query.pattern}
        if query.paths:
            payload["paths"] = list(query.paths)
        if query.flags:
            payload["flags"] = list(query.flags)
        if query.context_lines:
            payload["context_lines"] = query.context_lines

        response = self._client.request(
            self._method,
            self._url,
            json=payload,
            headers=self._headers,
            timeout=self._timeout,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:  # pragma: no cover - passthrough
            error_message = "HTTP search failed"
            raise RepoAdapterError(error_message) from error

        try:
            data = response.json()
        except json.JSONDecodeError as error:  # pragma: no cover - defensive
            error_message = "HTTP response was not valid JSON"
            raise RepoAdapterError(error_message) from error

        results = data.get("results", data)
        return [_normalize_hit(item) for item in results]


class CallableAdapter:
    """Adapter that delegates searches to an arbitrary Python callable."""

    def __init__(self, func: Callable[[SearchQuery], Iterable[Any]]) -> None:
        """Store the callable that will provide search results."""
        self._func = func

    def search(self, query: SearchQuery) -> list[RepoHit]:
        """Invoke the callable and normalize its output."""
        raw_results = self._func(query)
        return [_normalize_hit(item) for item in raw_results]


class RepositorySearcher:
    """High-level helper that retries ambiguous candidates sequentially."""

    def __init__(self, adapter: SearchAdapter) -> None:
        """Attach the adapter responsible for performing searches."""
        self._adapter = adapter

    def search(self, query: SearchQuery) -> list[RepoHit]:
        """Execute a direct search via the configured adapter."""
        return self._adapter.search(query)

    def search_candidates(
        self, candidates: Sequence[str], *, base_query: SearchQuery,
    ) -> list[RepoHit]:
        """Search a sequence of candidate patterns until a hit is found."""
        for candidate in candidates:
            hits = self._adapter.search(base_query.with_pattern(candidate))
            if hits:
                return hits
        return []


def _normalize_hit(item: Any) -> RepoHit:
    """Coerce various raw structures into a :class:`RepoHit`."""
    if isinstance(item, RepoHit):
        return item
    if isinstance(item, Mapping):
        try:
            return RepoHit.model_validate(item)
        except ValidationError as error:
            error_message = "Invalid hit payload"
            raise RepoAdapterError(error_message) from error
    error_message = (
        "Search results must be mappings or RepoHit instances, "
        f"got {type(item)!r}"
    )
    raise RepoAdapterError(error_message)
