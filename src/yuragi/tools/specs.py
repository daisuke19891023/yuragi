"""Parsers for specification diff tooling outputs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping as ABCMapping, Sequence
from typing import Any, cast
from collections.abc import Mapping

from pydantic import BaseModel, Field

from yuragi.core.models import Edge, EdgeType, Evidence, EvidenceType, Graph, Node, NodeType

MappingStrAny = Mapping[str, Any]


def _as_mapping(value: ABCMapping[Any, Any]) -> MappingStrAny:
    """Safely convert an ABC mapping to a ``Mapping[str, Any]``."""
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _maybe_mapping(value: Any) -> MappingStrAny | None:
    """Return ``value`` as a mapping when possible."""
    if isinstance(value, ABCMapping):
        return _as_mapping(cast("ABCMapping[Any, Any]", value))
    return None


class SpecChange(BaseModel):
    """A normalized representation of a specification change."""

    tool: str
    subject: str
    description: str
    severity: str = "unknown"
    locator: str = ""
    method: str | None = None
    path: str | None = None
    change_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_breaking(self) -> bool:
        """Return ``True`` when the change represents a breaking update."""
        severity = self.severity.lower()
        metadata_level = str(self.metadata.get("criticality_level", "")).lower()
        metadata_severity = str(self.metadata.get("severity", "")).lower()
        return (
            "breaking" in severity
            or severity in {"error", "critical"}
            or "breaking" in metadata_level
            or metadata_severity in {"breaking", "error", "critical"}
        )

    def to_evidence(self) -> Evidence:
        """Return an :class:`~yuragi.core.models.Evidence` instance for the change."""
        snippet = self.description or self.subject
        locator = self.locator or self.subject
        return Evidence(
            type=EvidenceType.SPEC,
            locator=locator,
            snippet=snippet,
            source_tool=self.tool,
        )


def _ensure_mapping(payload: str | Mapping[str, Any]) -> Mapping[str, Any]:
    """Convert a JSON string payload into a mapping."""
    if isinstance(payload, ABCMapping):
        return payload

    stripped = payload.strip()
    if not stripped:
        return {}

    try:
        decoded = json.loads(stripped)
    except json.JSONDecodeError as exc:  # pragma: no cover - invalid input guard
        error_message = "Invalid JSON payload supplied to spec parser"
        raise ValueError(error_message) from exc

    if not isinstance(decoded, ABCMapping):
        error_message = "JSON payload must decode into a mapping"
        raise TypeError(error_message)

    typed_decoded = _maybe_mapping(decoded)
    if typed_decoded is None:  # pragma: no cover - defensive fallback
        return {}
    return typed_decoded


def _collect_change_metadata(change: Mapping[str, Any]) -> dict[str, Any]:
    """Extract useful metadata fields from a change mapping."""
    metadata: dict[str, Any] = {}
    for key in ("id", "type", "change", "code"):  # common identifiers
        if key in change:
            metadata[key] = change[key]  # type: ignore[index]

    criticality = change.get("criticality")
    criticality_mapping = _maybe_mapping(criticality)
    if criticality_mapping is not None:
        criticality_level = criticality_mapping.get("level")
        if isinstance(criticality_level, str):
            metadata["criticality_level"] = criticality_level
        reason = criticality_mapping.get("reason")
        if isinstance(reason, str):
            metadata["criticality_reason"] = reason

    severity = change.get("severity")
    if isinstance(severity, str):
        metadata["severity"] = severity

    return metadata


def _oasdiff_change_from_mapping(
    method: str,
    path: str,
    change: Mapping[str, Any],
    *,
    default_severity: str,
) -> SpecChange:
    """Build a :class:`SpecChange` from an oasdiff change mapping."""
    description = str(
        change.get("text")
        or change.get("description")
        or change.get("message")
        or change.get("summary")
        or "",
    )
    severity_raw = (
        change.get("level")
        or change.get("severity")
        or change.get("criticality")
        or default_severity
    )
    severity = str(severity_raw or default_severity)

    locator = ""
    source = change.get("source")
    source_mapping = _maybe_mapping(source)
    if source_mapping is not None:
        for key in ("from", "path", "pointer"):
            candidate = source_mapping.get(key)
            if isinstance(candidate, str):
                locator = candidate
                break
    if not locator:
        pointer = change.get("pointer") or change.get("path")
        if isinstance(pointer, str):
            locator = pointer

    metadata = _collect_change_metadata(change)
    metadata.setdefault("method", method)
    metadata.setdefault("path", path)

    change_type_value = change.get("id") or change.get("code") or change.get("type")
    change_type = str(change_type_value) if change_type_value else ""

    return SpecChange(
        tool="oasdiff",
        subject=f"{method} {path}".strip(),
        description=description,
        severity=severity,
        locator=locator,
        method=method,
        path=path or None,
        change_type=change_type or None,
        metadata=metadata,
    )


def _iter_oasdiff_changes_for_method(
    method: str,
    path: str,
    method_mapping: MappingStrAny,
) -> Iterable[tuple[str, str, MappingStrAny]]:
    """Yield normalized change mappings for a single method."""
    method_changes = method_mapping.get("changes")
    if not isinstance(method_changes, Sequence):
        return

    typed_changes = cast("Sequence[Any]", method_changes)
    method_upper = method.upper()
    for raw_change in typed_changes:
        change_mapping = _maybe_mapping(raw_change)
        if change_mapping is None:
            continue
        yield method_upper, path, change_mapping


def _iter_oasdiff_method_changes(
    paths_mapping: MappingStrAny,
) -> Iterable[tuple[str, str, MappingStrAny]]:
    """Iterate over method-level change mappings from an oasdiff payload."""
    for path, path_info in paths_mapping.items():
        path_mapping = _maybe_mapping(path_info)
        if path_mapping is None:
            continue
        operations_mapping = _maybe_mapping(path_mapping.get("operations"))
        if operations_mapping is None:
            continue
        for method, method_info in operations_mapping.items():
            method_mapping = _maybe_mapping(method_info)
            if method_mapping is None:
                continue
            yield from _iter_oasdiff_changes_for_method(method, path, method_mapping)


def _parse_oasdiff_paths(paths_section: Any) -> list[SpecChange]:
    """Parse the nested paths/operations section of an oasdiff payload."""
    if not isinstance(paths_section, ABCMapping):
        return []

    typed_paths = _maybe_mapping(paths_section)
    if typed_paths is None:
        return []

    return [
        _oasdiff_change_from_mapping(method, path, change_mapping, default_severity="unknown")
        for method, path, change_mapping in _iter_oasdiff_method_changes(typed_paths)
    ]


def _parse_oasdiff_breaking(breaking_section: Any) -> list[SpecChange]:
    """Parse the breakingChanges section of an oasdiff payload."""
    if not isinstance(breaking_section, Sequence):
        return []

    typed_breaking = cast("Sequence[Any]", breaking_section)
    results: list[SpecChange] = []
    for raw_change in typed_breaking:
        change_mapping = _maybe_mapping(raw_change)
        if change_mapping is None:
            continue
        operation_info = change_mapping.get("operation") or change_mapping.get("source")
        method = "UNKNOWN"
        path = ""
        operation_mapping = _maybe_mapping(operation_info)
        if operation_mapping is not None:
            method_value = operation_mapping.get("method") or operation_mapping.get("httpMethod")
            if isinstance(method_value, str):
                method = method_value
            path_value = operation_mapping.get("path")
            if isinstance(path_value, str):
                path = path_value
        results.append(
            _oasdiff_change_from_mapping(
                method.upper(),
                path,
                change_mapping,
                default_severity="breaking",
            ),
        )
    return results



def parse_oasdiff(payload: str | Mapping[str, Any]) -> list[SpecChange]:
    """Parse oasdiff JSON output into :class:`SpecChange` objects."""
    document = _ensure_mapping(payload)
    changes: list[SpecChange] = []
    changes.extend(_parse_oasdiff_paths(document.get("paths")))
    changes.extend(_parse_oasdiff_breaking(document.get("breakingChanges")))
    return changes


def parse_buf_breaking(payload: str | Mapping[str, Any]) -> list[SpecChange]:
    """Parse buf breaking check JSON output into :class:`SpecChange` objects."""
    document = _ensure_mapping(payload)
    results = document.get("results") or document.get("file_annotations")
    changes: list[SpecChange] = []
    if isinstance(results, Sequence):
        typed_results = cast("Sequence[Any]", results)
        for raw_entry in typed_results:
            entry_mapping = _maybe_mapping(raw_entry)
            if entry_mapping is None:
                continue
            message = str(entry_mapping.get("message") or entry_mapping.get("description") or "")
            severity = str(entry_mapping.get("severity") or entry_mapping.get("category") or "unknown")
            path = str(entry_mapping.get("path") or entry_mapping.get("location") or "")
            change_type = str(entry_mapping.get("type") or entry_mapping.get("id") or "")
            metadata = _collect_change_metadata(entry_mapping)
            metadata.setdefault("path", path)
            metadata.setdefault("type", change_type)
            change = SpecChange(
                tool="buf",
                subject=path or change_type or message,
                description=message,
                severity=severity,
                locator=path,
                path=path or None,
                change_type=change_type or None,
                metadata=metadata,
            )
            changes.append(change)

    return changes


def _build_graphql_change(change: Mapping[str, Any], document_type: Any) -> SpecChange:
    """Normalize a graphql-inspector change entry."""
    message = str(change.get("message") or change.get("description") or "")
    subject = str(change.get("path") or change.get("name") or message)
    metadata = _collect_change_metadata(change)

    criticality = change.get("criticality")
    severity_value: str | None = None
    criticality_mapping = _maybe_mapping(criticality)
    if criticality_mapping is not None:
        level = criticality_mapping.get("level")
        if isinstance(level, str):
            severity_value = level
            metadata.setdefault("criticality_level", level)
        reason = criticality_mapping.get("reason")
        if isinstance(reason, str):
            metadata.setdefault("criticality_reason", reason)
    elif isinstance(criticality, str):
        severity_value = criticality

    severity = str(severity_value or document_type or "unknown")

    change_type_value = change.get("type") or change.get("code") or change.get("id")
    change_type = str(change_type_value) if change_type_value else ""
    metadata.setdefault("type", change_type)

    locator = str(change.get("path") or change.get("location") or subject)
    return SpecChange(
        tool="graphql-inspector",
        subject=subject,
        description=message,
        severity=severity,
        locator=locator,
        change_type=change_type or None,
        metadata=metadata,
    )


def parse_graphql_inspector(payload: str | Mapping[str, Any]) -> list[SpecChange]:
    """Parse graphql-inspector diff output into :class:`SpecChange` objects."""
    document = _ensure_mapping(payload)
    doc_type = document.get("type")
    top_level_changes = document.get("changes")
    changes: list[SpecChange] = []
    if isinstance(top_level_changes, Sequence):
        typed_top_level = cast("Sequence[Any]", top_level_changes)
        for raw_change in typed_top_level:
            change_mapping = _maybe_mapping(raw_change)
            if change_mapping is None:
                continue
            changes.append(_build_graphql_change(change_mapping, doc_type))
    else:
        for category in ("breaking", "dangerous", "safe"):
            section = document.get(category)
            if not isinstance(section, Sequence):
                continue
            typed_section = cast("Sequence[Any]", section)
            for raw_change in typed_section:
                change_mapping = _maybe_mapping(raw_change)
                if change_mapping is None:
                    continue
                changes.append(_build_graphql_change(change_mapping, doc_type))
    return changes


def build_spec_impact_graph(
    changes: Iterable[SpecChange],
    *,
    consumer_service: str | None = None,
    provider_service: str | None = None,
    base_confidence: float = 0.4,
) -> Graph:
    """Convert a list of spec changes into a :class:`Graph` impact view."""
    consumer = consumer_service or "unknown-consumer"
    provider = provider_service or "unknown-provider"
    node_index: dict[str, Node] = {}

    def _ensure_node(node: Node) -> None:
        node_index[node.id] = node

    consumer_node = Node(
        id=f"service:{consumer}",
        type=NodeType.SERVICE,
        name=consumer,
    )
    _ensure_node(consumer_node)

    provider_node = Node(
        id=f"service:{provider}",
        type=NodeType.SERVICE,
        name=provider,
    )
    _ensure_node(provider_node)

    edges_map: dict[str, Edge] = {}

    for change in changes:
        if not change.is_breaking:
            continue

        method = change.method or change.metadata.get("method")
        path = change.path or change.metadata.get("path")
        endpoint_label_parts: list[str] = []
        if isinstance(method, str) and method:
            endpoint_label_parts.append(method.upper())
        if isinstance(path, str) and path:
            endpoint_label_parts.append(str(path))
        subject = change.subject
        if not endpoint_label_parts and subject:
            endpoint_label_parts.append(subject)
        endpoint_label = " ".join(endpoint_label_parts) if endpoint_label_parts else subject

        endpoint_id = f"api:{provider}:{endpoint_label}"
        endpoint_node = Node(
            id=endpoint_id,
            type=NodeType.API_ENDPOINT,
            name=endpoint_label,
            attrs={
                "provider_service": provider,
                "tool": change.tool,
                "change_type": change.change_type,
            },
        )
        _ensure_node(endpoint_node)

        evidence = change.to_evidence()

        edge = edges_map.get(endpoint_id)
        if edge is None:
            edge = Edge(
                from_id=consumer_node.id,
                to_id=endpoint_id,
                type=EdgeType.CALLS,
                evidence=[evidence],
                confidence=base_confidence,
            )
            edges_map[endpoint_id] = edge
        else:
            edge.evidence.append(evidence)

    return Graph(nodes=list(node_index.values()), edges=list(edges_map.values()))


__all__ = [
    "SpecChange",
    "build_spec_impact_graph",
    "parse_buf_breaking",
    "parse_graphql_inspector",
    "parse_oasdiff",
]

