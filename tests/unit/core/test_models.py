"""Tests for yuragi.core.models."""

from __future__ import annotations

import itertools

from jsonschema import Draft202012Validator

from yuragi.core.models import (
    Edge,
    EdgeType,
    Evidence,
    EvidenceType,
    Graph,
    Node,
    NodeType,
)


def test_graph_schema_is_valid_json_schema() -> None:
    """The Graph model should emit a standards-compliant JSON Schema."""
    Draft202012Validator.check_schema(Graph.model_json_schema())


def test_graph_round_trip_with_sample_data() -> None:
    """A populated graph should survive a JSON round-trip."""
    nodes = [
        Node(id=f"node-{index}", type=NodeType.SERVICE, name=f"Service {index}")
        for index in range(10)
    ]

    evidences = [
        Evidence(
            type=EvidenceType.CODE,
            locator=f"src/service_{i}.py:10-20",
            snippet=f"def handler_{i}(...): pass",
            source_tool="rg",
        )
        for i in range(20)
    ]

    edges: list[Edge] = []
    evidence_cycle = itertools.cycle(evidences)
    for idx, (source, target) in enumerate(
        itertools.islice(itertools.permutations(nodes[:5], 2), 20),
    ):
        edges.append(
            Edge(
                from_id=source.id,
                to_id=target.id,
                type=EdgeType.CALLS,
                evidence=[next(evidence_cycle)],
                confidence=0.8 + (idx % 3) * 0.05,
            ),
        )

    graph = Graph(nodes=nodes, edges=edges)

    encoded = graph.model_dump_json()
    decoded = Graph.model_validate_json(encoded)

    assert decoded == graph
