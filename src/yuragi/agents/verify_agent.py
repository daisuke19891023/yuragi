"""Agent that validates CRUD actions by consulting repository and database tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agents.tracing import agent_span, function_span, trace

from yuragi.core.models import (
    CRUDAction,
    Edge,
    EdgeType,
    Evidence,
    EvidenceType,
    Graph,
    Node,
    NodeType,
)
from yuragi.core.scoring import calculate_confidence
from yuragi.tools.db import DatabaseAdapter, NEGATIVE_RESULT_CONFIDENCE_DELTA
from yuragi.tools.repo import RepoHit, RepositorySearcher, SearchQuery

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agents.tracing import Trace
else:  # pragma: no cover - used for runtime type hints only
    Sequence = tuple


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Clamp *value* within the inclusive ``[minimum, maximum]`` range."""
    return max(minimum, min(maximum, value))


def _edge_type_for_action(action: CRUDAction) -> EdgeType:
    """Return the graph edge type that corresponds to a CRUD verb."""
    if action.action == "SELECT":
        return EdgeType.READS
    return EdgeType.WRITES


def _service_node_id(service: str) -> str:
    return f"service:{service}"


def _table_node_id(table: str, schema: str | None) -> str:
    namespace = schema or "default"
    return f"table:{namespace}:{table}"


@dataclass
class VerifyAgent:
    """Verify CRUD actions using repository and database checks."""

    repository: RepositorySearcher
    database: DatabaseAdapter
    repo_source_tool: str = "repo-search"
    db_source_tool: str = "db-introspect"
    workflow_name: str = "verify_crud"
    agent_name: str = "verify-crud"
    last_trace: Trace | None = field(default=None, init=False, repr=False)

    def verify(
        self,
        actions: Sequence[CRUDAction],
        *,
        repo_base_query: SearchQuery | None = None,
        schema: str | None = None,
    ) -> Graph:
        """Validate *actions* and build a graph of confirmed relationships."""
        base_query = repo_base_query or SearchQuery(pattern="")
        node_index: dict[str, Node] = {}
        edges: list[Edge] = []

        with trace(self.workflow_name) as current_trace:
            self.last_trace = current_trace
            with agent_span(self.agent_name, output_type=Graph.__name__):
                for action in actions:
                    span_input = f"{action.service}:{action.action}:{action.table}"
                    with function_span("verify_action", input=span_input) as span:
                        verified = self._verify_single(action, base_query, schema)
                        span.span_data.output = (
                            {
                                "verified": verified is not None,
                                "confidence": verified.confidence if verified else 0.0,
                            }
                        )

                    if verified is None:
                        continue

                    service_node = node_index.get(verified.service_node.id)
                    if service_node is None:
                        node_index[verified.service_node.id] = verified.service_node

                    table_node = node_index.get(verified.table_node.id)
                    if table_node is None:
                        node_index[verified.table_node.id] = verified.table_node

                    edges.append(
                        Edge(
                            from_id=verified.service_node.id,
                            to_id=verified.table_node.id,
                            type=verified.edge_type,
                            evidence=list(verified.evidence),
                            confidence=verified.confidence,
                        ),
                    )

        return Graph(nodes=list(node_index.values()), edges=edges)

    def _verify_single(
        self,
        action: CRUDAction,
        base_query: SearchQuery,
        schema: str | None,
    ) -> _VerifiedEdge | None:
        candidates = self._candidate_patterns(action)
        with function_span("repo_search", input=", ".join(candidates)) as span:
            hits = self.repository.search_candidates(candidates, base_query=base_query)
            span.span_data.output = {"hits": len(hits)}
        if not hits:
            return None

        evidence: list[Evidence] = [self._hit_to_evidence(hit) for hit in hits]

        table_span_input = f"{schema or 'default'}.{action.table}"
        with function_span("introspect_table", input=table_span_input) as span:
            table_result = self.database.introspect_table(action.table, schema=schema)
            span.span_data.output = {"exists": table_result.exists}
        if not table_result.exists:
            return None

        evidence.append(
            Evidence(
                type=EvidenceType.CONFIG,
                locator=self._table_locator(table_result.table, table_result.schema),
                snippet=(
                    f"row_count={table_result.row_count}" if table_result.row_count is not None else None
                ),
                source_tool=self.db_source_tool,
            ),
        )

        confidence_adjustment = table_result.confidence_delta

        if action.columns:
            column_span_input = f"{table_span_input}:{','.join(action.columns)}"
            with function_span("introspect_columns", input=column_span_input) as span:
                column_result = self.database.introspect_columns(
                    action.table, schema=schema,
                )
                column_names = {column.name for column in column_result.columns}
                missing_columns = [column for column in action.columns if column not in column_names]
                span.span_data.output = {
                    "exists": column_result.exists,
                    "missing": missing_columns,
                }
            confidence_adjustment += column_result.confidence_delta
            if column_result.exists and not missing_columns:
                evidence.append(
                    Evidence(
                        type=EvidenceType.CONFIG,
                        locator=self._table_locator(column_result.table, column_result.schema),
                        snippet="columns=" + ",".join(sorted(column_names)),
                        source_tool=self.db_source_tool,
                    ),
                )
            elif missing_columns:
                confidence_adjustment += NEGATIVE_RESULT_CONFIDENCE_DELTA

        score = calculate_confidence(evidence)
        confidence = action.confidence
        for contribution in score.contributions:
            confidence += contribution.delta
        confidence = _clamp(confidence)
        confidence += confidence_adjustment
        confidence = _clamp(confidence)

        service_node = Node(
            id=_service_node_id(action.service),
            type=NodeType.SERVICE,
            name=action.service,
        )
        table_node = Node(
            id=_table_node_id(table_result.table, table_result.schema),
            type=NodeType.DB_TABLE,
            name=action.table,
            attrs={"schema": table_result.schema},
        )

        return _VerifiedEdge(
            service_node=service_node,
            table_node=table_node,
            edge_type=_edge_type_for_action(action),
            evidence=tuple(evidence),
            confidence=confidence,
        )

    def _candidate_patterns(self, action: CRUDAction) -> tuple[str, ...]:
        patterns: list[str] = [action.table]
        patterns.extend(f"{action.table}.{column}" for column in action.columns)
        patterns.extend(f"{action.table}.{key}" for key in action.where_keys)
        patterns.append(action.service)
        deduplicated: list[str] = []
        seen: set[str] = set()
        for pattern in patterns:
            if pattern and pattern not in seen:
                seen.add(pattern)
                deduplicated.append(pattern)
        return tuple(deduplicated)

    def _hit_to_evidence(self, hit: RepoHit) -> Evidence:
        locator = f"{hit.path}:L{hit.line_number}"
        return Evidence(
            type=EvidenceType.CODE,
            locator=locator,
            snippet=hit.line,
            source_tool=self.repo_source_tool,
        )

    def _table_locator(self, table: str, schema: str | None) -> str:
        if schema:
            return f"db:{schema}.{table}"
        return f"db:{table}"


@dataclass(frozen=True)
class _VerifiedEdge:
    service_node: Node
    table_node: Node
    edge_type: EdgeType
    evidence: tuple[Evidence, ...]
    confidence: float


__all__ = ["VerifyAgent"]

