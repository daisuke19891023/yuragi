from yuragi.core.models import EdgeType
from yuragi.tools.runtime import (
    RuntimeEdgeFlags,
    flags_from_otel_spans,
    flags_from_pg_stat_statements,
)


def test_pg_stat_statements_flags_detect_reads_and_writes() -> None:
    """pg_stat_statements entries should map to READS/WRITES flags."""
    records = [
        {"query": "SELECT * FROM public.orders WHERE id = $1", "calls": 12},
        {"query": "INSERT INTO public.orders (id, status) VALUES ($1, $2)", "calls": 2},
        {"query": "UPDATE other_table SET status = $1", "calls": 4},
    ]

    flags = flags_from_pg_stat_statements(records, table="public.orders")

    assert flags.has_reads is True
    assert flags.has_writes is True
    assert flags.has_calls is False
    assert flags.to_edge_types() == {EdgeType.READS, EdgeType.WRITES}


def test_otel_span_flags_detect_calls_and_db_operations() -> None:
    """OTel-like span exports should expose READS/WRITES/CALLS flags."""
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "checkout"}},
                    ],
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "HTTP GET inventory /items",
                                "attributes": [
                                    {
                                        "key": "peer.service",
                                        "value": {"stringValue": "inventory"},
                                    },
                                    {
                                        "key": "http.method",
                                        "value": {"stringValue": "GET"},
                                    },
                                ],
                            },
                            {
                                "name": "DB select",
                                "attributes": [
                                    {
                                        "key": "db.statement",
                                        "value": {
                                            "stringValue": "SELECT * FROM public.orders WHERE id = $1",
                                        },
                                    },
                                ],
                            },
                            {
                                "name": "DB update",
                                "attributes": [
                                    {"key": "db.operation", "value": {"stringValue": "UPDATE"}},
                                    {
                                        "key": "db.sql.table",
                                        "value": {"stringValue": "public.orders"},
                                    },
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }

    flags = flags_from_otel_spans(
        payload,
        source_service="checkout",
        peer_service="inventory",
        db_table="public.orders",
    )

    assert flags.has_reads is True
    assert flags.has_writes is True
    assert flags.has_calls is True
    assert flags.to_edge_types() == {
        EdgeType.READS,
        EdgeType.WRITES,
        EdgeType.CALLS,
    }


def test_runtime_edge_flags_merge_combines_indicators() -> None:
    """RuntimeEdgeFlags.merge should logically OR flag sets."""
    combined = RuntimeEdgeFlags(has_reads=True).merge(
        RuntimeEdgeFlags(has_calls=True), RuntimeEdgeFlags(has_writes=True),
    )

    assert combined.has_reads is True
    assert combined.has_writes is True
    assert combined.has_calls is True
    assert combined.to_edge_types() == {
        EdgeType.READS,
        EdgeType.WRITES,
        EdgeType.CALLS,
    }

