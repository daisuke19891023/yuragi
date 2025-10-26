"""Pydantic domain models for the yuragi dependency graph."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DEFAULT_SCHEMA_VERSION = "0.1.0"


class NodeType(StrEnum):
    """Supported node classifications within a graph."""

    SERVICE = "Service"
    API_ENDPOINT = "APIEndpoint"
    DB_TABLE = "DBTable"
    DB_COLUMN = "DBColumn"
    TOPIC = "Topic"
    CACHE_KEY_PATTERN = "CacheKeyPattern"
    GATEWAY_ROUTE = "GatewayRoute"
    IAC_RESOURCE = "IaCResource"
    BUILD_TARGET = "BuildTarget"
    DATASET = "Dataset"


class EdgeType(StrEnum):
    """Relationship types between nodes."""

    READS = "READS"
    WRITES = "WRITES"
    CALLS = "CALLS"
    PUBLISHES = "PUBLISHES"
    CONSUMES = "CONSUMES"
    ROUTES_TO = "ROUTES_TO"
    DEPENDS_ON = "DEPENDS_ON"
    GENERATES = "GENERATES"
    DERIVES_FROM = "DERIVES_FROM"


class EvidenceType(StrEnum):
    """The origin category for a piece of evidence."""

    CODE = "code"
    SPEC = "spec"
    LOG = "log"
    TRACE = "trace"
    CONFIG = "config"


type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class Evidence(BaseModel):
    """Supporting material that backs the existence of an edge."""

    type: EvidenceType
    locator: str
    snippet: str | None = None
    source_tool: str | None = None


def _empty_evidence_list() -> list[Evidence]:
    """Return a new list for storing evidence items."""
    return []


class CodeLocation(BaseModel):
    """A concrete source location that produced a CRUD action."""

    path: str
    span: str


def _empty_code_location_list() -> list[CodeLocation]:
    """Return a new list for storing code locations."""
    return []


class Node(BaseModel):
    """A vertex in the dependency graph."""

    id: str
    type: NodeType
    name: str
    attrs: dict[str, JSONValue] = Field(default_factory=dict)


class Edge(BaseModel):
    """A directed edge linking two nodes in the graph."""

    from_id: str
    to_id: str
    type: EdgeType
    evidence: list[Evidence] = Field(default_factory=_empty_evidence_list)
    confidence: float = Field(ge=0.0, le=1.0)


class CRUDAction(BaseModel):
    """A normalized CRUD interaction candidate derived from LLM output."""

    service: str
    table: str
    action: Literal["INSERT", "UPDATE", "DELETE", "SELECT"]
    columns: list[str]
    where_keys: list[str]
    code_locations: list[CodeLocation] = Field(default_factory=_empty_code_location_list)
    confidence: float = Field(ge=0.0, le=1.0)


def _empty_crud_action_list() -> list[CRUDAction]:
    """Return a new list for storing CRUD actions."""
    return []


class CRUDActionList(BaseModel):
    """Collection wrapper used for structured CRUD normalization results."""

    actions: list[CRUDAction] = Field(default_factory=_empty_crud_action_list)


def _empty_node_list() -> list[Node]:
    """Return a new list for storing nodes."""
    return []


def _empty_edge_list() -> list[Edge]:
    """Return a new list for storing edges."""
    return []


class Graph(BaseModel):
    """A dependency graph describing verified relationships between systems."""

    nodes: list[Node] = Field(default_factory=_empty_node_list)
    edges: list[Edge] = Field(default_factory=_empty_edge_list)
    schema_version: str = Field(default=DEFAULT_SCHEMA_VERSION)

    @model_validator(mode="after")
    def _validate_references(self) -> Graph:
        """Ensure edge endpoints exist and node identifiers are unique."""
        node_ids = {node.id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            error_message = "Graph nodes must have unique identifiers"
            raise ValueError(error_message)

        missing_sources: set[str] = set()
        missing_targets: set[str] = set()
        for edge in self.edges:
            if edge.from_id not in node_ids:
                missing_sources.add(edge.from_id)
            if edge.to_id not in node_ids:
                missing_targets.add(edge.to_id)
        if missing_sources or missing_targets:
            parts: list[str] = []
            if missing_sources:
                sources_message = (
                    "Edges reference unknown source nodes: "
                    + ", ".join(sorted(missing_sources))
                )
                parts.append(sources_message)
            if missing_targets:
                targets_message = (
                    "Edges reference unknown target nodes: "
                    + ", ".join(sorted(missing_targets))
                )
                parts.append(targets_message)
            error_message = "; ".join(parts)
            raise ValueError(error_message)

        return self


__all__ = [
    "DEFAULT_SCHEMA_VERSION",
    "CRUDAction",
    "CRUDActionList",
    "CodeLocation",
    "Edge",
    "EdgeType",
    "Evidence",
    "EvidenceType",
    "Graph",
    "Node",
    "NodeType",
]
