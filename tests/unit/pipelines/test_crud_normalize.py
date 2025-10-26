from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from yuragi.agents import NormalizationRequest, TermGlossary
from yuragi.core.errors import GraphValidationError
from yuragi.core.models import Edge, EdgeType, Evidence, EvidenceType, Graph, Node, NodeType
from yuragi.pipelines import CrudNormalizationPipeline, PipelineOutput, PipelineOutputFormat
from yuragi.tools.repo import SearchQuery

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


def _empty_calls() -> list[_OrchestratorCall]:
    return []


@dataclass(frozen=True)
class _OrchestratorCall:
    requests: tuple[NormalizationRequest | Mapping[str, object], ...]
    default_service: str | None
    glossary_overrides: TermGlossary | None
    repo_base_query: SearchQuery | None
    schema: str | None


@dataclass
class _StubOrchestrator:
    graph: Graph
    calls: list[_OrchestratorCall] = field(default_factory=_empty_calls)

    def run(
        self,
        requests: Sequence[NormalizationRequest | Mapping[str, object]],
        *,
        default_service: str | None = None,
        glossary_overrides: TermGlossary | None = None,
        repo_base_query: SearchQuery | None = None,
        schema: str | None = None,
    ) -> Graph:
        self.calls.append(
            _OrchestratorCall(
                requests=tuple(requests),
                default_service=default_service,
                glossary_overrides=glossary_overrides,
                repo_base_query=repo_base_query,
                schema=schema,
            ),
        )
        return self.graph


def _golden_graph() -> Graph:
    return Graph(
        nodes=[
            Node(
                id="table:public:billing_ledger",
                type=NodeType.DB_TABLE,
                name="billing_ledger",
                attrs={"schema": "public"},
            ),
            Node(
                id="service:BillingService",
                type=NodeType.SERVICE,
                name="BillingService",
            ),
        ],
        edges=[
            Edge(
                from_id="service:BillingService",
                to_id="table:public:billing_ledger",
                type=EdgeType.WRITES,
                evidence=[
                    Evidence(
                        type=EvidenceType.CONFIG,
                        locator="db:public.billing_ledger",
                        snippet="row_count=256",
                        source_tool="db-introspect",
                    ),
                    Evidence(
                        type=EvidenceType.CODE,
                        locator="src/billing.py:L25",
                        snippet="ledger.write(order)",
                        source_tool="repo-search",
                    ),
                ],
                confidence=0.82,
            ),
        ],
    )


def test_pipeline_writes_golden_outputs(tmp_path: Path) -> None:
    """Golden test ensuring deterministic serialization for the pipeline."""
    graph = _golden_graph()
    orchestrator = _StubOrchestrator(graph=graph)
    pipeline = CrudNormalizationPipeline(orchestrator=cast("Any", orchestrator))

    json_path = tmp_path / "graph.json"
    ndjson_path = tmp_path / "graph.ndjson"

    requests = [
        NormalizationRequest(description="Billing writes totals", service="BillingService"),
    ]

    glossary = TermGlossary(service_aliases={"billing": "BillingService"})

    result = pipeline.run(
        requests,
        default_service="BillingService",
        glossary_overrides=glossary,
        repo_base_query=SearchQuery(pattern="ledger"),
        schema="public",
        outputs=[
            PipelineOutput(PipelineOutputFormat.JSON, json_path),
            PipelineOutput(PipelineOutputFormat.NDJSON, ndjson_path),
        ],
    )

    assert len(orchestrator.calls) == 1
    call = orchestrator.calls[0]
    descriptions = [
        req.description
        for req in call.requests
        if isinstance(req, NormalizationRequest)
    ]
    assert descriptions == ["Billing writes totals"]
    assert call.default_service == "BillingService"
    assert call.glossary_overrides == glossary
    assert call.schema == "public"

    expected_graph = Graph(
        nodes=[
            Node(
                id="service:BillingService",
                type=NodeType.SERVICE,
                name="BillingService",
            ),
            Node(
                id="table:public:billing_ledger",
                type=NodeType.DB_TABLE,
                name="billing_ledger",
                attrs={"schema": "public"},
            ),
        ],
        edges=[
            Edge(
                from_id="service:BillingService",
                to_id="table:public:billing_ledger",
                type=EdgeType.WRITES,
                evidence=[
                    Evidence(
                        type=EvidenceType.CODE,
                        locator="src/billing.py:L25",
                        snippet="ledger.write(order)",
                        source_tool="repo-search",
                    ),
                    Evidence(
                        type=EvidenceType.CONFIG,
                        locator="db:public.billing_ledger",
                        snippet="row_count=256",
                        source_tool="db-introspect",
                    ),
                ],
                confidence=0.82,
            ),
        ],
    )

    assert result.model_dump(mode="json") == expected_graph.model_dump(mode="json")

    expected_json = json.dumps(
        expected_graph.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    assert json_path.read_text(encoding="utf-8") == expected_json

    ndjson_entries = [
        {
            "record_type": "graph",
            "graph": {
                "schema_version": expected_graph.schema_version,
                "node_count": len(expected_graph.nodes),
                "edge_count": len(expected_graph.edges),
            },
        },
        {
            "record_type": "node",
            "node": expected_graph.nodes[0].model_dump(mode="json"),
        },
        {
            "record_type": "node",
            "node": expected_graph.nodes[1].model_dump(mode="json"),
        },
        {
            "record_type": "edge",
            "edge": expected_graph.edges[0].model_dump(mode="json"),
        },
    ]
    expected_ndjson = "\n".join(
        json.dumps(entry, ensure_ascii=False, sort_keys=True) for entry in ndjson_entries
    ) + "\n"
    assert ndjson_path.read_text(encoding="utf-8") == expected_ndjson


def test_pipeline_requires_evidence() -> None:
    """The pipeline rejects graphs that contain edges without evidence."""
    graph = Graph(
        nodes=[
            Node(id="service:A", type=NodeType.SERVICE, name="A"),
            Node(id="table:public:resource", type=NodeType.DB_TABLE, name="resource"),
        ],
        edges=[
            Edge(
                from_id="service:A",
                to_id="table:public:resource",
                type=EdgeType.READS,
                evidence=[],
                confidence=0.5,
            ),
        ],
    )
    pipeline = CrudNormalizationPipeline(
        orchestrator=cast("Any", _StubOrchestrator(graph=graph)),
    )

    with pytest.raises(GraphValidationError, match="missing evidence"):
        pipeline.run([NormalizationRequest(description="A reads resource")])
