from yuragi.core.models import EdgeType, EvidenceType, NodeType
from yuragi.tools.specs import (
    build_spec_impact_graph,
    parse_buf_breaking,
    parse_graphql_inspector,
    parse_oasdiff,
)


def test_parse_oasdiff_extracts_breaking_changes() -> None:
    """Oasdiff payloads should be normalized into SpecChange instances."""
    payload = {
        "paths": {
            "/pets": {
                "operations": {
                    "get": {
                        "changes": [
                            {
                                "id": "response.status.changed",
                                "level": "breaking",
                                "text": "Response status 200 changed to 204",
                                "source": {
                                    "from": "openapi-old.yaml#/paths/~1pets/get/responses/200",
                                },
                            },
                        ],
                    },
                },
            },
        },
    }

    changes = parse_oasdiff(payload)

    assert len(changes) == 1
    change = changes[0]
    assert change.tool == "oasdiff"
    assert change.subject == "GET /pets"
    assert change.is_breaking
    assert change.method == "GET"
    assert change.path == "/pets"


def test_parse_buf_breaking_understands_result_entries() -> None:
    """Buf breaking change JSON should normalize to SpecChange with severity."""
    payload = {
        "results": [
            {
                "message": "Field example.Foo.bar was deleted.",
                "path": "proto/example.proto:12:5",
                "type": "FIELD_NO_DELETE",
                "severity": "ERROR",
            },
        ],
    }

    changes = parse_buf_breaking(payload)

    assert len(changes) == 1
    change = changes[0]
    assert change.tool == "buf"
    assert change.subject == "proto/example.proto:12:5"
    assert change.is_breaking


def test_parse_graphql_inspector_handles_change_list() -> None:
    """GraphQL Inspector diff payloads should produce breaking SpecChanges."""
    payload = {
        "changes": [
            {
                "type": "FIELD_REMOVED",
                "path": "Query.oldField",
                "message": "Field Query.oldField was removed.",
                "criticality": {
                    "level": "BREAKING",
                    "reason": "Removing fields is a breaking change.",
                },
            },
        ],
    }

    changes = parse_graphql_inspector(payload)

    assert len(changes) == 1
    change = changes[0]
    assert change.tool == "graphql-inspector"
    assert change.is_breaking
    assert change.subject == "Query.oldField"


def test_build_spec_impact_graph_creates_call_edges() -> None:
    """Breaking changes should be transformed into CALLS impact edges."""
    oas_change = parse_oasdiff(
        {
            "breakingChanges": [
                {
                    "id": "request.parameter.increased.min",
                    "level": "breaking",
                    "text": "Parameter `limit` minimum increased.",
                    "operation": {"method": "GET", "path": "/pets"},
                },
            ],
        },
    )[0]

    buf_change = parse_buf_breaking(
        {
            "results": [
                {
                    "message": "Service example.Inventory renamed.",
                    "path": "proto/example.proto:22:1",
                    "type": "SERVICE_NO_RENAME",
                    "severity": "ERROR",
                },
            ],
        },
    )[0]

    graphql_change = parse_graphql_inspector(
        {
            "changes": [
                {
                    "type": "FIELD_REMOVED",
                    "path": "Mutation.createOrder",
                    "message": "Mutation.createOrder was removed.",
                    "criticality": {"level": "BREAKING"},
                },
            ],
        },
    )[0]

    graph = build_spec_impact_graph(
        [oas_change, buf_change, graphql_change],
        consumer_service="checkout",
        provider_service="inventory",
        base_confidence=0.6,
    )

    assert {node.type for node in graph.nodes} >= {NodeType.SERVICE, NodeType.API_ENDPOINT}
    consumer_ids = [node.id for node in graph.nodes if node.name == "checkout"]
    assert consumer_ids == ["service:checkout"]

    assert len(graph.edges) == 3
    for edge in graph.edges:
        assert edge.type == EdgeType.CALLS
        assert edge.from_id == "service:checkout"
        assert edge.confidence == 0.6
        assert edge.evidence
        evidence = edge.evidence[0]
        assert evidence.type == EvidenceType.SPEC

