"""Workflow orchestrator that connects normalization and verification agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agents.tracing import agent_span, function_span, trace

from yuragi.core.models import CRUDAction, CRUDActionList, Edge, Graph

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping, Sequence

    from agents.tracing import Trace

    from yuragi.agents.normalize_agent import NormalizationRequest, NormalizeAgent, TermGlossary
    from yuragi.agents.verify_agent import VerifyAgent
    from yuragi.tools.repo import SearchQuery


class OrchestrationError(RuntimeError):
    """Raised when the orchestrator cannot recover from repeated failures."""


def _clone_edge(edge: Edge) -> Edge:
    """Return a shallow copy of *edge* to avoid mutating the source graph."""
    return Edge(
        from_id=edge.from_id,
        to_id=edge.to_id,
        type=edge.type,
        evidence=list(edge.evidence),
        confidence=edge.confidence,
    )


@dataclass
class CrudWorkflowOrchestrator:
    """Coordinate the Normalize and Verify agents with retries and guardrails."""

    normalize_agent: NormalizeAgent
    verify_agent: VerifyAgent
    confidence_threshold: float = 0.7
    require_evidence: bool = True
    max_normalize_attempts: int = 2
    max_verify_attempts: int = 2
    workflow_name: str = "crud_orchestration"
    agent_name: str = "crud-orchestrator"
    last_trace: Trace | None = field(default=None, init=False, repr=False)

    def run(
        self,
        requests: Sequence[NormalizationRequest | Mapping[str, object]],
        *,
        default_service: str | None = None,
        glossary_overrides: TermGlossary | None = None,
        repo_base_query: SearchQuery | None = None,
        schema: str | None = None,
    ) -> Graph:
        """Execute the CRUD workflow and return a verified graph."""
        with trace(self.workflow_name) as current_trace:
            self.last_trace = current_trace
            with agent_span(self.agent_name, output_type=Graph.__name__):
                actions = self._normalize_with_retry(
                    requests,
                    default_service=default_service,
                    glossary_overrides=glossary_overrides,
                )

                if not actions.actions:
                    return Graph()

                graph = self._verify_with_retry(
                    actions.actions,
                    repo_base_query=repo_base_query,
                    schema=schema,
                )

                if not graph.edges:
                    return graph

                return self._filter_graph(graph)

    def _normalize_with_retry(
        self,
        requests: Sequence[NormalizationRequest | Mapping[str, object]],
        *,
        default_service: str | None,
        glossary_overrides: TermGlossary | None,
    ) -> CRUDActionList:
        errors: list[Exception] = []
        for attempt in range(1, self.max_normalize_attempts + 1):
            attempt_label = f"attempt={attempt}"
            with function_span("normalize", input=attempt_label) as span:
                try:
                    result = self.normalize_agent.normalize(
                        requests,
                        default_service=default_service,
                        glossary_overrides=glossary_overrides,
                    )
                except Exception as error:  # pragma: no cover - defensive
                    errors.append(error)
                    span.span_data.output = {"status": "error", "reason": str(error)}
                else:
                    span.span_data.output = {
                        "status": "success",
                        "actions": len(result.actions),
                    }
                    return result
        if errors:
            return CRUDActionList()
        message = "Normalization failed without raising an exception"
        raise OrchestrationError(message)

    def _verify_with_retry(
        self,
        actions: Sequence[CRUDAction],
        *,
        repo_base_query: SearchQuery | None,
        schema: str | None,
    ) -> Graph:
        errors: list[Exception] = []
        for attempt in range(1, self.max_verify_attempts + 1):
            attempt_label = f"attempt={attempt}"
            with function_span("verify", input=attempt_label) as span:
                try:
                    result = self.verify_agent.verify(
                        actions,
                        repo_base_query=repo_base_query,
                        schema=schema,
                    )
                except Exception as error:  # pragma: no cover - defensive
                    errors.append(error)
                    span.span_data.output = {"status": "error", "reason": str(error)}
                else:
                    span.span_data.output = {
                        "status": "success",
                        "edges": len(result.edges),
                    }
                    return result
        if errors:
            return Graph()
        message = "Verification failed without raising an exception"
        raise OrchestrationError(message)

    def _filter_graph(self, graph: Graph) -> Graph:
        with function_span(
            "apply_thresholds",
            input=f"threshold={self.confidence_threshold},require_evidence={self.require_evidence}",
        ) as span:
            filtered_edges: list[Edge] = []
            for edge in graph.edges:
                if self.require_evidence and not edge.evidence:
                    continue
                if edge.confidence < self.confidence_threshold:
                    continue
                filtered_edges.append(_clone_edge(edge))

            if not filtered_edges:
                span.span_data.output = {"remaining_edges": 0}
                return Graph()

            node_ids = {edge.from_id for edge in filtered_edges} | {
                edge.to_id for edge in filtered_edges
            }
            filtered_nodes = [node for node in graph.nodes if node.id in node_ids]
            span.span_data.output = {"remaining_edges": len(filtered_edges), "nodes": len(filtered_nodes)}
            return Graph(nodes=filtered_nodes, edges=filtered_edges, schema_version=graph.schema_version)


__all__ = ["CrudWorkflowOrchestrator", "OrchestrationError"]

