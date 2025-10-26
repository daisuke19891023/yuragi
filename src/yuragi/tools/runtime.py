"""Runtime evidence helpers for confirming observed relationships."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast
from collections.abc import Iterable, Iterator, Mapping, Sequence

from yuragi.core.models import EdgeType


@dataclass(frozen=True)
class RuntimeEdgeFlags:
    """Boolean indicators describing which edge types were observed at runtime."""

    has_reads: bool = False
    has_writes: bool = False
    has_calls: bool = False

    def merge(self, *others: RuntimeEdgeFlags) -> RuntimeEdgeFlags:
        """Return a combined set of flags with logical OR semantics."""
        merged = self
        for other in others:
            merged = RuntimeEdgeFlags(
                has_reads=merged.has_reads or other.has_reads,
                has_writes=merged.has_writes or other.has_writes,
                has_calls=merged.has_calls or other.has_calls,
            )
        return merged

    def to_edge_types(self) -> set[EdgeType]:
        """Return the :class:`~yuragi.core.models.EdgeType` values marked as present."""
        edge_types: set[EdgeType] = set()
        if self.has_reads:
            edge_types.add(EdgeType.READS)
        if self.has_writes:
            edge_types.add(EdgeType.WRITES)
        if self.has_calls:
            edge_types.add(EdgeType.CALLS)
        return edge_types


def flags_from_pg_stat_statements(
    records: Iterable[Mapping[str, Any]], *, table: str,
) -> RuntimeEdgeFlags:
    """Derive READS/WRITES flags from ``pg_stat_statements`` style records."""
    if not table or not table.strip():
        error_message = "table must be a non-empty identifier"
        raise ValueError(error_message)

    normalized_table_tokens = _candidate_table_tokens(table)

    has_reads = False
    has_writes = False
    for record in records:
        query = record.get("query")
        if not isinstance(query, str):
            continue

        calls = _extract_positive_int(record, "calls")
        if calls <= 0:
            continue

        normalized_query = _normalize_sql(query)
        if not _query_mentions_table(normalized_query, normalized_table_tokens):
            continue

        operation = _classify_sql_operation(normalized_query)
        if operation == "read":
            has_reads = True
        elif operation == "write":
            has_writes = True

        if has_reads and has_writes:
            break

    return RuntimeEdgeFlags(has_reads=has_reads, has_writes=has_writes)


def flags_from_otel_spans(
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    source_service: str | None = None,
    db_table: str | None = None,
    peer_service: str | None = None,
) -> RuntimeEdgeFlags:
    """Extract READS/WRITES/CALLS flags from OpenTelemetry-like span exports."""
    normalized_table_tokens = _candidate_table_tokens(db_table) if db_table else None
    normalized_peer = _normalize_identifier(peer_service) if peer_service else None
    normalized_source = _normalize_identifier(source_service) if source_service else None

    has_reads = False
    has_writes = False
    has_calls = False

    for attributes in _iter_span_attribute_mappings(payload, normalized_source):
        if normalized_table_tokens and _span_targets_table(
            attributes, normalized_table_tokens,
        ):
            operation = _operation_from_span(attributes)
            if operation == "read":
                has_reads = True
            elif operation == "write":
                has_writes = True

        if normalized_peer:
            peer = _resolve_peer_service(attributes)
            if peer and _normalize_identifier(peer) == normalized_peer:
                has_calls = True

        if has_reads and has_writes and has_calls:
            return RuntimeEdgeFlags(
                has_reads=True,
                has_writes=True,
                has_calls=True,
            )

    return RuntimeEdgeFlags(
        has_reads=has_reads,
        has_writes=has_writes,
        has_calls=has_calls,
    )


def _iter_span_attribute_mappings(
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    normalized_source: str | None,
) -> Iterator[Mapping[str, str]]:
    resource_spans: Sequence[Any]
    if isinstance(payload, Mapping):
        resource_spans = cast("Sequence[Any]", payload.get("resourceSpans") or ())
    else:
        resource_spans = cast("Sequence[Any]", payload)
    for resource_span_obj in resource_spans:
        if not isinstance(resource_span_obj, Mapping):
            continue
        resource_span_mapping = cast("Mapping[str, Any]", resource_span_obj)
        resource_service = _extract_resource_service(resource_span_mapping)
        if normalized_source and resource_service != normalized_source:
            continue
        for span in _spans_from_resource_span(resource_span_mapping):
            attributes = _attributes_to_mapping(span.get("attributes"))
            if attributes:
                yield attributes


def _extract_resource_service(resource_span: Mapping[str, Any]) -> str | None:
    resource_obj = resource_span.get("resource")
    if not isinstance(resource_obj, Mapping):
        return None
    resource = cast("Mapping[str, Any]", resource_obj)
    attributes = _attributes_to_mapping(resource.get("attributes"))
    return _normalize_identifier(attributes.get("service.name"))


def _spans_from_resource_span(
    resource_span: Mapping[str, Any],
) -> Iterator[Mapping[str, Any]]:
    scope_spans_obj = resource_span.get("scopeSpans") or resource_span.get(
        "instrumentationLibrarySpans",
    )
    if not isinstance(scope_spans_obj, Sequence):
        return iter(())
    scope_spans = cast("Sequence[Any]", scope_spans_obj)
    spans_to_yield: list[Mapping[str, Any]] = []
    for scope_span_obj in scope_spans:
        if not isinstance(scope_span_obj, Mapping):
            continue
        scope_span = cast("Mapping[str, Any]", scope_span_obj)
        spans_obj = scope_span.get("spans")
        if not isinstance(spans_obj, Sequence):
            continue
        spans_to_yield.extend(
            cast("Mapping[str, Any]", span_mapping)
            for span_mapping in cast("Sequence[Any]", spans_obj)
            if isinstance(span_mapping, Mapping)
        )
    return iter(spans_to_yield)


def _attributes_to_mapping(attributes: Any) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not isinstance(attributes, Sequence):
        return mapping
    for attribute_obj in cast("Sequence[Any]", attributes):
        if not isinstance(attribute_obj, Mapping):
            continue
        attribute = cast("Mapping[str, Any]", attribute_obj)
        key = attribute.get("key")
        if not isinstance(key, str):
            continue
        value = _extract_attribute_value(attribute.get("value"))
        if value is not None:
            mapping[key] = value
    return mapping


def _extract_attribute_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Mapping):
        mapping_value = cast("Mapping[str, object]", value)
        for field in (
            "stringValue",
            "intValue",
            "doubleValue",
            "boolValue",
        ):
            inner_value = mapping_value.get(field)
            if inner_value is None:
                continue
            if isinstance(inner_value, bool):
                return "true" if inner_value else "false"
            return str(inner_value)
    return None


def _resolve_peer_service(attributes: Mapping[str, str]) -> str | None:
    for key in ("peer.service", "net.peer.name", "peer.service.name"):
        value = attributes.get(key)
        if value:
            return value
    host = attributes.get("http.host")
    if host:
        return host
    url = attributes.get("http.url")
    if url:
        match = re.match(r"https?://([^/]+)/", url)
        if match:
            return match.group(1)
    return None


def _operation_from_span(attributes: Mapping[str, str]) -> str | None:
    operation = attributes.get("db.operation")
    if operation:
        operation_normalized = operation.lower()
        if operation_normalized in {"select", "fetch", "query"}:
            return "read"
        if operation_normalized in {"insert", "update", "delete", "merge"}:
            return "write"
    statement = attributes.get("db.statement")
    if statement:
        normalized_statement = _normalize_sql(statement)
        return _classify_sql_operation(normalized_statement)
    return None


def _span_targets_table(
    attributes: Mapping[str, str], normalized_table_tokens: frozenset[str],
) -> bool:
    table_attribute = attributes.get("db.sql.table") or attributes.get("db.name")
    if table_attribute:
        normalized_table = _normalize_identifier(table_attribute)
        if normalized_table in normalized_table_tokens:
            return True

    statement = attributes.get("db.statement")
    if statement:
        normalized_statement = _normalize_sql(statement)
        return _query_mentions_table(normalized_statement, normalized_table_tokens)

    return False


def _candidate_table_tokens(table: str | None) -> frozenset[str]:
    if not table:
        return frozenset()
    normalized = _normalize_identifier(table)
    if normalized is None:
        return frozenset()
    candidates: set[str] = {normalized}
    if "." in normalized:
        _, _, table_name = normalized.rpartition(".")
        candidates.add(table_name)
    return frozenset(candidates)


def _normalize_identifier(identifier: str | None) -> str | None:
    if identifier is None:
        return None
    return re.sub(r"[`\"']", "", identifier).strip().lower()


def _normalize_sql(sql: str) -> str:
    stripped = re.sub(r"[`\"']", "", sql)
    collapsed = re.sub(r"\s+", " ", stripped)
    return collapsed.strip().lower()


def _query_mentions_table(
    normalized_query: str, table_tokens: frozenset[str],
) -> bool:
    for token in table_tokens:
        pattern = rf"\b{re.escape(token)}\b"
        if re.search(pattern, normalized_query):
            return True
    return False


def _classify_sql_operation(normalized_query: str) -> str | None:
    if not normalized_query:
        return None
    match = re.match(r"^(\w+)", normalized_query)
    if not match:
        return None
    keyword = match.group(1)
    if keyword in {"select", "show", "with"}:
        return "read"
    if keyword in {"insert", "update", "delete", "merge"}:
        return "write"
    return None


def _extract_positive_int(mapping: Mapping[str, Any], key: str) -> int:
    value = mapping.get(key, 0)
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return 0


__all__ = [
    "RuntimeEdgeFlags",
    "flags_from_otel_spans",
    "flags_from_pg_stat_statements",
]

