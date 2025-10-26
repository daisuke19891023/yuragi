from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from math import isclose

if TYPE_CHECKING:
    from collections.abc import Callable
else:  # pragma: no cover - only used for typing compatibility
    Callable = Any

from yuragi.agents import VerifyAgent
from yuragi.core.models import CRUDAction
from yuragi.tools.db import (
    ColumnIntrospectionResult,
    ColumnMetadata,
    DatabaseAdapter,
    TableIntrospectionResult,
)
from yuragi.tools.repo import RepoHit, RepositorySearcher, SearchAdapter, SearchQuery


def _empty_table_call_log() -> list[tuple[str, str | None]]:
    return []


def _empty_column_call_log() -> list[tuple[str, str | None]]:
    return []


@dataclass
class StubSearchAdapter(SearchAdapter):
    """Return predetermined hits for repository verification."""

    responses: dict[str, list[RepoHit]]
    calls: list[str] = field(default_factory=list[str])

    def search(self, query: SearchQuery) -> list[RepoHit]:  # type: ignore[override]
        """Record the pattern searched and return configured hits."""
        self.calls.append(query.pattern)
        return self.responses.get(query.pattern, [])


@dataclass
class StubDatabaseAdapter(DatabaseAdapter):
    """Track database introspection calls for verification tests."""

    table_result: TableIntrospectionResult
    column_result_factory: Callable[[str], ColumnIntrospectionResult]
    table_calls: list[tuple[str, str | None]] = field(default_factory=_empty_table_call_log)
    column_calls: list[tuple[str, str | None]] = field(default_factory=_empty_column_call_log)

    def introspect_table(
        self, table: str, *, schema: str | None = None,
    ) -> TableIntrospectionResult:  # type: ignore[override]
        """Return the configured table introspection result."""
        self.table_calls.append((table, schema))
        return self.table_result

    def introspect_columns(
        self, table: str, *, schema: str | None = None,
    ) -> ColumnIntrospectionResult:  # type: ignore[override]
        """Return the configured column introspection result."""
        self.column_calls.append((table, schema))
        return self.column_result_factory(table)

    def explain(self, sql: str) -> Any:  # pragma: no cover - not used in tests
        """Prevent explain from being called in these tests."""
        raise AssertionError(sql)


def _make_repo_hit(path: str, line_number: int, line: str) -> RepoHit:
    return RepoHit(path=path, line_number=line_number, line=line)


def test_verify_agent_builds_graph_with_confident_edge() -> None:
    """Verify agent should aggregate evidence and reach confirmed confidence."""
    adapter = StubSearchAdapter(
        {
            "orders_ledger": [_make_repo_hit("src/jobs/order.py", 42, "orders_ledger.write")],
        },
    )
    searcher = RepositorySearcher(adapter)

    table_result = TableIntrospectionResult(
        table="orders_ledger",
        schema=None,
        exists=True,
        row_count=128,
        confidence_delta=0.0,
    )

    def column_factory(_: str) -> ColumnIntrospectionResult:
        return ColumnIntrospectionResult(
            table="orders_ledger",
            schema=None,
            exists=True,
            columns=(
                ColumnMetadata(name="order_id", data_type="text", nullable=False, default=None),
                ColumnMetadata(name="total", data_type="numeric", nullable=False, default=None),
            ),
            confidence_delta=0.0,
        )

    database = StubDatabaseAdapter(table_result, column_factory)

    agent = VerifyAgent(repository=searcher, database=database)

    action = CRUDAction(
        service="OrderService",
        table="orders_ledger",
        action="INSERT",
        columns=["order_id", "total"],
        where_keys=["order_id"],
        code_locations=[],
        confidence=0.8,
    )

    graph = agent.verify([action], repo_base_query=SearchQuery(pattern="", paths=("src",)))

    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1

    edge = graph.edges[0]
    assert edge.type.name == "WRITES"
    assert edge.confidence >= 0.7
    assert len(edge.evidence) >= 2
    assert any(item.type is not None and item.type.value == "code" for item in edge.evidence)
    assert any(item.source_tool == agent.db_source_tool for item in edge.evidence)

    # Repository search should precede database calls and stop after the first hit.
    assert adapter.calls[0] == "orders_ledger"
    assert len(adapter.calls) == 1
    assert database.table_calls == [("orders_ledger", None)]
    assert database.column_calls == [("orders_ledger", None)]


def test_verify_agent_filters_false_positive_when_no_repo_hits() -> None:
    """Candidates without repository evidence should be dropped from the graph."""
    adapter = StubSearchAdapter({})
    searcher = RepositorySearcher(adapter)

    table_result = TableIntrospectionResult(
        table="ghost_table",
        schema=None,
        exists=True,
        row_count=0,
        confidence_delta=0.0,
    )

    def column_factory(_: str) -> ColumnIntrospectionResult:
        return ColumnIntrospectionResult(
            table="ghost_table",
            schema=None,
            exists=False,
            columns=(),
            confidence_delta=-0.3,
        )

    database = StubDatabaseAdapter(table_result, column_factory)

    agent = VerifyAgent(repository=searcher, database=database)

    action = CRUDAction(
        service="GhostService",
        table="ghost_table",
        action="DELETE",
        columns=["id"],
        where_keys=[],
        code_locations=[],
        confidence=0.6,
    )

    graph = agent.verify([action])

    assert graph.nodes == []
    assert graph.edges == []
    assert database.table_calls == []
    assert database.column_calls == []


def test_verify_agent_penalizes_missing_columns() -> None:
    """Missing columns should reduce the resulting edge confidence."""
    adapter = StubSearchAdapter(
        {
            "audit_log": [_make_repo_hit("src/reporting.py", 9, "audit_log.lookup")],
        },
    )
    searcher = RepositorySearcher(adapter)

    table_result = TableIntrospectionResult(
        table="audit_log",
        schema="analytics",
        exists=True,
        row_count=None,
        confidence_delta=0.0,
    )

    def column_factory(_: str) -> ColumnIntrospectionResult:
        return ColumnIntrospectionResult(
            table="audit_log",
            schema="analytics",
            exists=True,
            columns=(
                ColumnMetadata(name="account_id", data_type="uuid", nullable=False, default=None),
            ),
            confidence_delta=0.0,
        )

    database = StubDatabaseAdapter(table_result, column_factory)

    agent = VerifyAgent(repository=searcher, database=database)

    action = CRUDAction(
        service="ReportingDashboard",
        table="audit_log",
        action="SELECT",
        columns=["account_id", "missing_column"],
        where_keys=[],
        code_locations=[],
        confidence=0.75,
    )

    graph = agent.verify([action])

    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert isclose(edge.confidence, 0.7, rel_tol=1e-6)
    assert any(item.source_tool == agent.db_source_tool for item in edge.evidence)

