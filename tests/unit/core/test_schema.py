"""Tests for yuragi.core.schema utilities."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast

from jsonschema import Draft202012Validator

from yuragi.core import DEFAULT_SCHEMA_VERSION
from yuragi.core.schema import (
    build_graph_json_schema,
    detect_breaking_changes,
    export_graph_schema,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_build_graph_json_schema_includes_version_and_is_valid() -> None:
    """Graph schema exposes the embedded version and validates."""
    schema = build_graph_json_schema()

    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"]["const"] == DEFAULT_SCHEMA_VERSION


def test_export_graph_schema_writes_pretty_json(tmp_path: Path) -> None:
    """Exported schema persists as formatted JSON on disk."""
    output_path = tmp_path / "graph_schema.json"
    schema = export_graph_schema(output_path)

    assert output_path.read_text(encoding="utf-8").strip().startswith("{")
    assert schema == build_graph_json_schema()


def test_detect_breaking_changes_reports_field_add_remove_and_type_changes() -> None:
    """Breaking changes report additions, removals, and type shifts."""
    base_schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["name"],
    }

    added_schema = deepcopy(base_schema)
    properties_for_added = cast("dict[str, Any]", added_schema["properties"])
    properties_for_added["extra"] = {"type": "string"}

    removed_schema = deepcopy(base_schema)
    properties_for_removed = cast("dict[str, Any]", removed_schema["properties"])
    properties_for_removed.pop("count", None)

    type_changed_schema = deepcopy(base_schema)
    properties_for_type_change = cast(
        "dict[str, Any]", type_changed_schema["properties"],
    )
    properties_for_type_change["count"] = {"type": "string"}

    added_changes = detect_breaking_changes(base_schema, added_schema)
    removed_changes = detect_breaking_changes(base_schema, removed_schema)
    type_changes = detect_breaking_changes(base_schema, type_changed_schema)

    assert any(change.change_type == "field_added" for change in added_changes)
    assert any(change.change_type == "field_removed" for change in removed_changes)
    assert any(change.change_type == "type_changed" for change in type_changes)
