"""Agent responsible for normalizing ambiguous CRUD descriptions."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING, Literal, cast

from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from collections.abc import MutableMapping as _MutableMapping
from collections.abc import Sequence as _Sequence

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, MutableMapping, Sequence
else:  # pragma: no cover - aliases keep runtime dependencies minimal
    Iterable = _Iterable
    Mapping = _Mapping
    MutableMapping = _MutableMapping
    Sequence = _Sequence

from agents import Agent
from agents.tracing import agent_span, function_span, generation_span, trace

from yuragi.core.models import CRUDAction, CRUDActionList, CodeLocation
from yuragi.llm.prompts import (
    NormalizationFewShot,
    build_normalization_system_prompt,
    default_normalization_few_shots,
)
from yuragi.core.safety import scrub_for_logging

CRUDVerb = Literal["INSERT", "UPDATE", "DELETE", "SELECT"]

_ACTION_SYNONYMS: dict[CRUDVerb, set[str]] = {
    "INSERT": {
        "add",
        "adding",
        "append",
        "create",
        "creating",
        "insert",
        "inserting",
        "new record",
        "persist",
        "record a",
        "write",
        "writing",
    },
    "UPDATE": {
        "bump",
        "flip",
        "modify",
        "patch",
        "refresh",
        "set",
        "toggle",
        "update",
        "updating",
    },
    "DELETE": {
        "cleanup",
        "clear",
        "delete",
        "deleting",
        "drop",
        "prune",
        "purge",
        "remove",
        "scrub",
        "truncate",
    },
    "SELECT": {
        "check",
        "fetch",
        "inspect",
        "lookup",
        "query",
        "read",
        "scan",
        "select",
        "validate",
    },
}


def _normalise_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


@dataclass
class TermGlossary:
    """Lookup tables for normalising service, table and column names."""

    service_aliases: MutableMapping[str, str] = field(default_factory=dict[str, str])
    table_aliases: MutableMapping[str, str] = field(default_factory=dict[str, str])
    column_aliases: MutableMapping[str, str] = field(default_factory=dict[str, str])

    def __post_init__(self) -> None:
        """Normalise alias keys so lookups become case insensitive."""
        self.service_aliases = {self._canonical_key(key): value for key, value in self.service_aliases.items()}
        self.table_aliases = {self._canonical_key(key): value for key, value in self.table_aliases.items()}
        self.column_aliases = {self._canonical_key(key): value for key, value in self.column_aliases.items()}

    @staticmethod
    def _canonical_key(key: str) -> str:
        return _normalise_key(key)

    def canonical_service(self, value: str | None) -> str | None:
        """Return the canonical service name for *value*, if available."""
        if value is None:
            return None
        key = self._canonical_key(value)
        return self.service_aliases.get(key, value)

    def canonical_table(self, value: str | None) -> str | None:
        """Return the canonical table name for *value*, if available."""
        if value is None:
            return None
        key = self._canonical_key(value)
        return self.table_aliases.get(key, value)

    def canonical_column(self, value: str) -> str:
        """Return the canonical column name for *value*, if available."""
        key = self._canonical_key(value)
        return self.column_aliases.get(key, value)

    def merge(self, other: TermGlossary | None) -> TermGlossary:
        """Combine this glossary with *other* and return the merged view."""
        if other is None:
            return self
        return TermGlossary(
            service_aliases={**self.service_aliases, **other.service_aliases},
            table_aliases={**self.table_aliases, **other.table_aliases},
            column_aliases={**self.column_aliases, **other.column_aliases},
        )


@dataclass
class NormalizationRequest:
    """Input payload describing an ambiguous CRUD behaviour."""

    description: str
    service: str | None = None
    table_hint: str | None = None
    columns_hint: Sequence[str] = field(default_factory=tuple)
    where_hint: Sequence[str] = field(default_factory=tuple)
    path: str | None = None
    span: str | None = None

    def code_locations(self) -> list[CodeLocation]:
        """Return any source code locations embedded in the request."""
        if self.path and self.span:
            return [CodeLocation(path=self.path, span=self.span)]
        if self.path:
            return [CodeLocation(path=self.path, span="")]  # span optional for heuristics
        return []


def _to_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _to_str_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence_value = cast("Sequence[object]", value)
        return tuple(str(item) for item in sequence_value)
    return (str(value),)


def _coerce_request(item: NormalizationRequest | Mapping[str, object]) -> NormalizationRequest:
    if isinstance(item, NormalizationRequest):
        return item
    data: dict[str, object] = dict(item)
    return NormalizationRequest(
        description=str(data.get("description", "")),
        service=_to_optional_str(data.get("service")),
        table_hint=_to_optional_str(data.get("table_hint")),
        columns_hint=_to_str_tuple(data.get("columns_hint")),
        where_hint=_to_str_tuple(data.get("where_hint")),
        path=_to_optional_str(data.get("path")),
        span=_to_optional_str(data.get("span")),
    )


@dataclass
class NormalizeAgent:
    """Normalize ambiguous CRUD descriptions into structured actions."""

    glossary: TermGlossary = field(
        default_factory=lambda: TermGlossary(
            service_aliases={
                "billing svc": "BillingService",
                "order api": "OrderAPI",
            },
            table_aliases={
                "ledger": "billing_ledger",
                "session store": "session_store",
                "audit trail": "audit_log",
            },
            column_aliases={
                "checkout total": "checkout_total",
                "status flag": "status_flag",
            },
        ),
    )
    few_shots: Sequence[NormalizationFewShot] = field(default_factory=default_normalization_few_shots)
    workflow_name: str = "normalize_crud"
    agent_name: str = "normalize-crud"
    _agent: Agent = field(init=False)
    last_trace = None

    def __post_init__(self) -> None:
        """Instantiate the Agents SDK object with static prompts."""
        instructions = build_normalization_system_prompt(self.glossary, self.few_shots)
        self._agent = Agent(
            name=self.agent_name,
            instructions=instructions,
            output_type=CRUDActionList,
        )

    def normalize(
        self,
        requests: Sequence[NormalizationRequest | Mapping[str, object]],
        *,
        default_service: str | None = None,
        glossary_overrides: TermGlossary | None = None,
    ) -> CRUDActionList:
        """Normalize the provided descriptions and return structured CRUD actions."""
        effective_glossary = self.glossary.merge(glossary_overrides)
        coerced_requests = [_coerce_request(item) for item in requests]

        with trace(self.workflow_name) as current_trace:
            self.last_trace = current_trace
            with agent_span(self.agent_name, output_type=CRUDActionList.__name__):
                actions = [
                    self._normalize_single(request, effective_glossary, default_service)
                    for request in coerced_requests
                ]
                result = CRUDActionList(actions=actions)
                generation_inputs = [
                    {"role": "user", "content": req.description} for req in coerced_requests
                ]
                generation_outputs = [action.model_dump() for action in result.actions]
                with generation_span(
                    input=scrub_for_logging(generation_inputs),
                    output=scrub_for_logging(generation_outputs),
                    model=None,
                ):
                    pass
        return result

    def _normalize_single(
        self,
        request: NormalizationRequest,
        glossary: TermGlossary,
        default_service: str | None,
    ) -> CRUDAction:
        text = request.description
        with function_span("infer_action", input=scrub_for_logging(text)) as span:
            action, action_score = self._infer_action(text)
            span.span_data.output = scrub_for_logging(action)

        with function_span("infer_table", input=scrub_for_logging(text)) as span:
            table, table_score = self._infer_table(text, request.table_hint, glossary)
            span.span_data.output = scrub_for_logging(table)

        with function_span("infer_service", input=scrub_for_logging(text)) as span:
            service, service_score = self._infer_service(text, request.service, glossary, default_service)
            span.span_data.output = scrub_for_logging(service)

        with function_span("infer_columns", input=scrub_for_logging(text)) as span:
            columns = self._infer_columns(text, request.columns_hint, glossary)
            span.span_data.output = scrub_for_logging(
                ", ".join(columns) if columns else "",
            )

        with function_span("infer_where", input=scrub_for_logging(text)) as span:
            where_keys = self._infer_where_keys(text, request.where_hint, glossary)
            span.span_data.output = scrub_for_logging(
                ", ".join(where_keys) if where_keys else "",
            )

        confidence = min(1.0, 0.35 + action_score + table_score + service_score)
        return CRUDAction(
            service=service,
            table=table,
            action=action,
            columns=columns,
            where_keys=where_keys,
            code_locations=request.code_locations(),
            confidence=confidence,
        )

    def _infer_action(self, text: str) -> tuple[CRUDVerb, float]:
        lowered = text.lower()
        for action, keywords in _ACTION_SYNONYMS.items():
            for keyword in keywords:
                if keyword in lowered:
                    base = 0.35 if action != "SELECT" else 0.25
                    return action, base
        # Default to SELECT with conservative confidence when nothing matches.
        return "SELECT", 0.15

    def _infer_table(
        self,
        text: str,
        hint: str | None,
        glossary: TermGlossary,
    ) -> tuple[str, float]:
        if hint:
            canonical = glossary.canonical_table(hint)
            table_name = canonical or hint
            return table_name, 0.25

        lowered = text.lower()
        for alias, canonical in glossary.table_aliases.items():
            if alias in lowered:
                return canonical or alias, 0.25

        match = re.search(r"([a-zA-Z0-9_]+)\s+table", text)
        if match:
            table_name = _sanitize_identifier(match.group(1))
            canonical = glossary.canonical_table(table_name)
            return canonical or table_name, 0.2

        fallback = glossary.canonical_table("observations") or "observations"
        return fallback, 0.05

    def _infer_service(
        self,
        text: str,
        provided: str | None,
        glossary: TermGlossary,
        default_service: str | None,
    ) -> tuple[str, float]:
        if provided:
            canonical = glossary.canonical_service(provided) or provided
            return canonical, 0.2

        camel_match = re.search(r"([A-Z][A-Za-z0-9]+(?:Service|Job|Worker)?)", text)
        if camel_match:
            candidate = camel_match.group(1)
            has_suffix = candidate.endswith(("Service", "Job", "Worker"))
            has_internal_upper = any(char.isupper() for char in candidate[1:])
            if has_suffix or has_internal_upper:
                canonical = glossary.canonical_service(candidate) or candidate
                return canonical, 0.15

        if default_service:
            canonical = glossary.canonical_service(default_service) or default_service
            return canonical, 0.1

        return "UnknownService", 0.05

    def _infer_columns(
        self,
        text: str,
        hints: Sequence[str],
        glossary: TermGlossary,
    ) -> list[str]:
        candidates = [_sanitize_identifier(item) for item in hints]
        lowered = text.lower()
        candidates.extend(
            canonical for alias, canonical in glossary.column_aliases.items() if alias in lowered
        )
        candidates.extend(
            _sanitize_identifier(f"{match}_id") for match in re.findall(r"([a-zA-Z0-9_]+)_id", text)
        )
        return _unique_preserve_order(glossary.canonical_column(item) for item in candidates)

    def _infer_where_keys(
        self,
        text: str,
        hints: Sequence[str],
        glossary: TermGlossary,
    ) -> list[str]:
        candidates = [_sanitize_identifier(item) for item in hints]
        lowered = text.lower()
        key_keywords = ["key", "identifier", "id", "primary"]
        for keyword in key_keywords:
            if keyword in lowered and "id" not in candidates:
                candidates.append("id")
        return _unique_preserve_order(glossary.canonical_column(item) for item in candidates)


def _sanitize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_").lower()


def _unique_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if not item:
            continue
        if item not in seen:
            ordered.append(item)
            seen.add(item)
    return ordered


__all__ = [
    "NormalizationRequest",
    "NormalizeAgent",
    "TermGlossary",
]
