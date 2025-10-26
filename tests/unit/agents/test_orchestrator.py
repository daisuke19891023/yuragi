from __future__ import annotations

from dataclasses import dataclass, field
from types import MethodType
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping, Sequence

from yuragi.agents import (
    CrudWorkflowOrchestrator,
    NormalizationRequest,
    NormalizeAgent,
    VerifyAgent,
)
from yuragi.core.models import CRUDAction, CRUDActionList, Edge, EdgeType, Evidence, EvidenceType, Graph, Node, NodeType
from yuragi.tools.db import (
    ColumnIntrospectionResult,
    ColumnMetadata,
    DatabaseAdapter,
    TableIntrospectionResult,
)
from yuragi.tools.repo import RepoHit, RepositorySearcher, SearchAdapter, SearchQuery


def _make_repo_hit(path: str, line_number: int, line: str) -> RepoHit:
    return RepoHit(path=path, line_number=line_number, line=line)


def _empty_str_list() -> list[str]:
    return []


@dataclass
class _StubSearchAdapter(SearchAdapter):
    responses: dict[str, list[RepoHit]]
    calls: list[str] = field(default_factory=_empty_str_list)

    def search(self, query: SearchQuery) -> list[RepoHit]:  # type: ignore[override]
        self.calls.append(query.pattern)
        return self.responses.get(query.pattern, [])


@dataclass
class _StubDatabaseAdapter(DatabaseAdapter):
    table_result: TableIntrospectionResult
    column_result: ColumnIntrospectionResult
    calls: list[str] = field(default_factory=_empty_str_list)

    def introspect_table(  # type: ignore[override]
        self,
        table: str,
        *,
        schema: str | None = None,
    ) -> TableIntrospectionResult:
        self.calls.append(f"table:{schema or 'default'}:{table}")
        return self.table_result

    def introspect_columns(  # type: ignore[override]
        self,
        table: str,
        *,
        schema: str | None = None,
    ) -> ColumnIntrospectionResult:
        self.calls.append(f"columns:{schema or 'default'}:{table}")
        return self.column_result

    def explain(self, sql: str) -> Any:  # pragma: no cover - not used
        raise AssertionError(sql)


def test_orchestrator_handoffs_and_filters_graph() -> None:
    """Normalize requests, verify evidence and filter graph edges."""
    normalize_agent = NormalizeAgent()

    requests: Sequence[NormalizationRequest | Mapping[str, object]] = [
        NormalizationRequest(
            description="Billing service writes totals into the ledger table during checkout.",
            service="BillingService",
            table_hint="ledger",
            columns_hint=["checkout total"],
            where_hint=["order_id"],
            path="src/billing.py",
            span="L10-L40",
        ),
    ]

    repo_adapter = _StubSearchAdapter(
        {
            "billing_ledger": [_make_repo_hit("src/billing.py", 25, "billing_ledger.write")],
        },
    )
    searcher = RepositorySearcher(repo_adapter)

    table_result = TableIntrospectionResult(
        table="billing_ledger",
        schema=None,
        exists=True,
        row_count=256,
        confidence_delta=0.0,
    )
    column_result = ColumnIntrospectionResult(
        table="billing_ledger",
        schema=None,
        exists=True,
        columns=(
            ColumnMetadata(name="order_id", data_type="uuid", nullable=False, default=None),
            ColumnMetadata(name="checkout_total", data_type="numeric", nullable=False, default=None),
        ),
        confidence_delta=0.0,
    )
    database = _StubDatabaseAdapter(table_result=table_result, column_result=column_result)

    verify_agent = VerifyAgent(repository=searcher, database=database)
    orchestrator = CrudWorkflowOrchestrator(normalize_agent=normalize_agent, verify_agent=verify_agent)

    graph = orchestrator.run(requests, default_service="BillingService")

    assert len(graph.nodes) == 2
    assert {node.type for node in graph.nodes} == {NodeType.SERVICE, NodeType.DB_TABLE}
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.type == EdgeType.WRITES
    assert edge.confidence >= orchestrator.confidence_threshold
    assert edge.evidence
    assert repo_adapter.calls
    assert database.calls


def test_orchestrator_retries_and_applies_threshold() -> None:
    """Retry both stages and keep only confident edges."""
    normalize_agent = NormalizeAgent()

    call_tracker = {"count": 0}

    def flaky_normalize(
        _self: NormalizeAgent,
        _requests: Sequence[Mapping[str, object]],
        **_kwargs: Any,
    ) -> CRUDActionList:
        call_tracker["count"] += 1
        if call_tracker["count"] == 1:
            raise RuntimeError("transient failure")
        return CRUDActionList(
            actions=[
                CRUDAction(
                    service="ServiceA",
                    table="table_a",
                    action="INSERT",
                    columns=[],
                    where_keys=[],
                    code_locations=[],
                    confidence=0.9,
                ),
            ],
        )

    normalize_agent.normalize = MethodType(flaky_normalize, normalize_agent)  # type: ignore[assignment]

    verify_agent = VerifyAgent(repository=RepositorySearcher(_StubSearchAdapter({})), database=_StubDatabaseAdapter(
        table_result=TableIntrospectionResult(
            table="table_a",
            schema=None,
            exists=True,
            row_count=None,
            confidence_delta=0.0,
        ),
        column_result=ColumnIntrospectionResult(
            table="table_a",
            schema=None,
            exists=True,
            columns=(),
            confidence_delta=0.0,
        ),
    ))

    verify_state = {"called": False}

    def custom_verify(
        _self: VerifyAgent,
        _actions: Sequence[CRUDAction],
        **_kwargs: Any,
    ) -> Graph:
        if not verify_state["called"]:
            verify_state["called"] = True
            raise RuntimeError("retry me")
        return Graph(
            nodes=[
                Node(id="service:ServiceA", type=NodeType.SERVICE, name="ServiceA"),
                Node(id="table:default:table_a", type=NodeType.DB_TABLE, name="table_a"),
            ],
            edges=[
                Edge(
                    from_id="service:ServiceA",
                    to_id="table:default:table_a",
                    type=EdgeType.WRITES,
                    evidence=[
                        Evidence(
                            type=EvidenceType.CODE,
                            locator="src/app.py:L10",
                            snippet="table_a.write()",
                            source_tool="repo",
                        ),
                    ],
                    confidence=0.65,
                ),
                Edge(
                    from_id="service:ServiceA",
                    to_id="table:default:table_a",
                    type=EdgeType.WRITES,
                    evidence=[
                        Evidence(
                            type=EvidenceType.CONFIG,
                            locator="db:table_a",
                            snippet=None,
                            source_tool="db",
                        ),
                    ],
                    confidence=0.8,
                ),
            ],
        )

    verify_agent.verify = MethodType(custom_verify, verify_agent)  # type: ignore[assignment]

    orchestrator = CrudWorkflowOrchestrator(
        normalize_agent=normalize_agent,
        verify_agent=verify_agent,
        confidence_threshold=0.7,
    )

    graph = orchestrator.run([{"description": "ignored"}])

    assert call_tracker["count"] == 2
    assert len(graph.edges) == 1
    assert graph.edges[0].confidence >= 0.7
    assert graph.edges[0].evidence


def test_orchestrator_returns_empty_graph_when_retries_exhausted() -> None:
    """Fallback to an empty graph when normalization never succeeds."""
    normalize_agent = NormalizeAgent()

    def always_fail(
        _self: NormalizeAgent,
        _requests: Sequence[Mapping[str, object]],
        **_kwargs: Any,
    ) -> CRUDActionList:
        raise RuntimeError("fatal")

    normalize_agent.normalize = MethodType(always_fail, normalize_agent)  # type: ignore[assignment]

    verify_agent = VerifyAgent(repository=RepositorySearcher(_StubSearchAdapter({})), database=_StubDatabaseAdapter(
        table_result=TableIntrospectionResult(
            table="table_a",
            schema=None,
            exists=False,
            row_count=None,
            confidence_delta=0.0,
        ),
        column_result=ColumnIntrospectionResult(
            table="table_a",
            schema=None,
            exists=False,
            columns=(),
            confidence_delta=0.0,
        ),
    ))

    orchestrator = CrudWorkflowOrchestrator(
        normalize_agent=normalize_agent,
        verify_agent=verify_agent,
        max_normalize_attempts=2,
        max_verify_attempts=1,
    )

    graph = orchestrator.run([{"description": "any"}])

    assert graph.nodes == []
    assert graph.edges == []
