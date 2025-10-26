"""Utilities for exporting and diffing yuragi JSON Schemas."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict

from .models import DEFAULT_SCHEMA_VERSION, Graph

SCHEMA_DRAFT_URL = "https://json-schema.org/draft/2020-12/schema"


def build_graph_json_schema() -> dict[str, Any]:
    """Return the JSON Schema representation for :class:`Graph`."""
    schema = Graph.model_json_schema()
    schema.setdefault("$schema", SCHEMA_DRAFT_URL)
    schema["title"] = "YuragiGraph"
    schema.setdefault("$id", "https://schemas.yuragi.dev/graph.json")

    graph_properties = schema.get("properties", {})
    if "schema_version" in graph_properties:
        graph_properties["schema_version"]["const"] = DEFAULT_SCHEMA_VERSION
        graph_properties["schema_version"].setdefault(
            "description",
            "Version of the yuragi graph schema this payload conforms to.",
        )

    Draft202012Validator.check_schema(schema)
    return schema


def export_graph_schema(path: Path | str) -> dict[str, Any]:
    """Write the graph JSON Schema to *path* and return it."""
    schema = build_graph_json_schema()
    path_obj = Path(path)
    path_obj.write_text(
        json.dumps(schema, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return schema


class FieldSnapshot(BaseModel):
    """A simplified view of a field in a JSON Schema tree."""

    model_config = ConfigDict(frozen=True)

    type_repr: tuple[str, ...]
    required: bool


class SchemaChange(BaseModel):
    """A breaking change detected between two schema versions."""

    model_config = ConfigDict(frozen=True)

    path: str
    change_type: str
    detail: str


def detect_breaking_changes(
    old_schema: Mapping[str, Any],
    new_schema: Mapping[str, Any],
) -> list[SchemaChange]:
    """Detect breaking changes between *old_schema* and *new_schema*."""
    old_snapshot = _collect_field_snapshots(old_schema)
    new_snapshot = _collect_field_snapshots(new_schema)

    changes: list[SchemaChange] = []

    for path, info in old_snapshot.items():
        if path not in new_snapshot:
            changes.append(
                SchemaChange(
                    path=path,
                    change_type="field_removed",
                    detail="Field was removed from schema.",
                ),
            )
            continue

        new_info = new_snapshot[path]
        if info.type_repr != new_info.type_repr:
            changes.append(
                SchemaChange(
                    path=path,
                    change_type="type_changed",
                    detail=(
                        "Field type changed from "
                        f"{info.type_repr} to {new_info.type_repr}."
                    ),
                ),
            )
        if info.required and not new_info.required:
            changes.append(
                SchemaChange(
                    path=path,
                    change_type="required_relaxed",
                    detail="Field changed from required to optional.",
                ),
            )
        if not info.required and new_info.required:
            changes.append(
                SchemaChange(
                    path=path,
                    change_type="required_added",
                    detail="Field changed from optional to required.",
                ),
            )

    added_fields = [
        SchemaChange(
            path=added_path,
            change_type="field_added",
            detail="New field added to schema.",
        )
        for added_path in sorted(new_snapshot.keys() - old_snapshot.keys())
    ]
    changes.extend(added_fields)

    return changes


def _collect_field_snapshots(schema: Mapping[str, Any]) -> dict[str, FieldSnapshot]:
    """Flatten schema *schema* into a dictionary of field snapshots."""
    resolver = _SchemaResolver(schema)
    snapshots: dict[str, FieldSnapshot] = {}
    resolver.collect(schema, path="", into=snapshots)
    return snapshots


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    """Return a mapping view of *value* when available."""
    if isinstance(value, Mapping):
        return cast("Mapping[str, Any]", value)
    return None


class _SchemaResolver:
    """Resolve $ref entries and collect type information."""

    def __init__(self, schema: Mapping[str, Any]) -> None:
        self._schema = schema
        self._definitions = schema.get("$defs", {})

    def resolve_ref(self, ref: str) -> Mapping[str, Any]:
        if not ref.startswith("#/$defs/"):
            error_message = f"Unsupported $ref target: {ref}"
            raise ValueError(error_message)
        key = ref.split("/", maxsplit=2)[-1]
        try:
            return self._definitions[key]
        except KeyError as exc:
            error_message = f"Unresolved schema definition: {ref}"
            raise ValueError(error_message) from exc

    def normalize(self, node: Mapping[str, Any]) -> Mapping[str, Any]:
        if "$ref" in node:
            return self.resolve_ref(node["$ref"])
        return node

    def collect(
        self,
        node: Mapping[str, Any],
        *,
        path: str,
        into: dict[str, FieldSnapshot],
        required: bool | None = None,
    ) -> None:
        resolved = self.normalize(node)
        node_type = resolved.get("type")

        if node_type == "object":
            self._collect_object(resolved, path=path, into=into)
        elif node_type == "array":
            self._collect_array(resolved, path=path, into=into, required=required)

        self._collect_combinators(resolved, path=path, into=into, required=required)

    def _collect_object(
        self,
        schema: Mapping[str, Any],
        *,
        path: str,
        into: dict[str, FieldSnapshot],
    ) -> None:
        properties = _as_mapping(schema.get("properties", {}))
        if properties is None:
            return
        required_raw = schema.get("required", [])
        required_fields = {
            entry for entry in required_raw if isinstance(entry, str)
        }
        for name, child in properties.items():
            child_mapping = _as_mapping(child)
            if child_mapping is None:
                continue
            child_path = f"{path}.{name}" if path else name
            is_required = name in required_fields
            child_resolved = self.normalize(child_mapping)
            into[child_path] = FieldSnapshot(
                type_repr=_describe_type(child_resolved, self),
                required=is_required,
            )
            self.collect(
                child_mapping,
                path=child_path,
                into=into,
                required=is_required,
            )

    def _collect_array(
        self,
        schema: Mapping[str, Any],
        *,
        path: str,
        into: dict[str, FieldSnapshot],
        required: bool | None,
    ) -> None:
        items_mapping = _as_mapping(schema.get("items"))
        if items_mapping is None:
            return

        item_path = f"{path}[]" if path else "[]"
        normalized_items = self.normalize(items_mapping)
        into[item_path] = FieldSnapshot(
            type_repr=_describe_type(normalized_items, self),
            required=required if required is not None else False,
        )
        self.collect(
            items_mapping,
            path=item_path,
            into=into,
            required=required,
        )

    def _collect_combinators(
        self,
        schema: Mapping[str, Any],
        *,
        path: str,
        into: dict[str, FieldSnapshot],
        required: bool | None,
    ) -> None:
        for keyword in ("anyOf", "allOf", "oneOf"):
            variants_raw = schema.get(keyword)
            if not isinstance(variants_raw, list):
                continue
            variant_candidates = cast("Sequence[object]", variants_raw)
            for variant_obj in variant_candidates:
                variant_mapping = _as_mapping(variant_obj)
                if variant_mapping is None:
                    continue
                self.collect(
                    variant_mapping,
                    path=path,
                    into=into,
                    required=required,
                )


def _describe_type(node: Mapping[str, Any], resolver: _SchemaResolver) -> tuple[str, ...]:
    """Return a tuple describing the type of *node*."""
    if "$ref" in node:
        node = resolver.normalize(node)

    types: set[str] = set()
    node_type = node.get("type")
    if isinstance(node_type, list):
        node_type_values = cast("Sequence[object]", node_type)
        types.update(str(item) for item in node_type_values)
    elif isinstance(node_type, str):
        types.add(node_type)

    for keyword in ("anyOf", "allOf", "oneOf"):
        variants = node.get(keyword)
        if isinstance(variants, list):
            variant_candidates = cast("Sequence[object]", variants)
            for variant_obj in variant_candidates:
                variant_mapping = _as_mapping(variant_obj)
                if variant_mapping is None:
                    continue
                types.update(_describe_type(variant_mapping, resolver))

    if "enum" in node:
        enum_values = ",".join(sorted(map(str, node["enum"])))
        types.add(f"enum({enum_values})")

    if not types and "const" in node:
        types.add(f"const({node['const']!r})")

    return tuple(sorted(types))


__all__ = [
    "FieldSnapshot",
    "SchemaChange",
    "build_graph_json_schema",
    "detect_breaking_changes",
    "export_graph_schema",
]
