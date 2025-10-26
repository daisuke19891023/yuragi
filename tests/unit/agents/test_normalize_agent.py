from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator
else:  # pragma: no cover - runtime alias
    from collections.abc import Generator as _Generator

    Generator = _Generator

import pytest

from agents.tracing import Span, Trace, set_trace_processors
from agents.tracing.processor_interface import TracingProcessor
from agents.tracing.processors import default_processor

from yuragi.agents import NormalizeAgent, NormalizationRequest, TermGlossary
from yuragi.core.models import CRUDActionList


@dataclass
class _CaptureProcessor(TracingProcessor):
    traces_started: list[str] = field(default_factory=list[str])
    traces_finished: list[str] = field(default_factory=list[str])
    spans_started: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    spans_finished: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def on_trace_start(self, trace: Trace) -> None:  # type: ignore[override]
        self.traces_started.append(trace.trace_id)

    def on_trace_end(self, trace: Trace) -> None:  # type: ignore[override]
        self.traces_finished.append(trace.trace_id)

    def on_span_start(self, span: Span[Any]) -> None:  # type: ignore[override]
        exported = span.export()
        if exported is not None:
            self.spans_started.append(exported)

    def on_span_end(self, span: Span[Any]) -> None:  # type: ignore[override]
        exported = span.export()
        if exported is not None:
            self.spans_finished.append(exported)

    def shutdown(self) -> None:  # type: ignore[override]
        return None

    def force_flush(self) -> None:  # type: ignore[override]
        return None


@pytest.fixture
def capture_processor() -> Generator[_CaptureProcessor]:
    """Capture spans emitted during a test run and restore defaults afterwards."""
    processor = _CaptureProcessor()
    set_trace_processors([processor])
    try:
        yield processor
    finally:
        # Restore the default OpenAI processor to avoid leaking state between tests.
        set_trace_processors([default_processor()])


def test_normalize_agent_handles_ambiguous_requests(capture_processor: _CaptureProcessor) -> None:
    """The agent should normalize three distinct ambiguous descriptions."""
    agent = NormalizeAgent()

    requests = [
        NormalizationRequest(
            description="The onboarding job adds a fresh record into the ledger table for each signup.",
            service="OnboardingJob",
            path="src/jobs/onboarding.py",
            span="L10-L42",
        ),
        NormalizationRequest(
            description="Our janitor task prunes expired sessions from the session store every night.",
            service="TokenSweeper",
        ),
        NormalizationRequest(
            description="The reporting dashboard looks up the audit trail by account_id before drawing charts.",
            table_hint="audit trail",
            columns_hint=["account_id"],
        ),
    ]

    result = agent.normalize(requests, default_service="ReportingDashboard")

    assert isinstance(result, CRUDActionList)
    assert len(result.actions) == 3

    by_service = {action.service: action for action in result.actions}

    onboarding = by_service["OnboardingJob"]
    assert onboarding.action == "INSERT"
    assert onboarding.table == "billing_ledger"
    assert onboarding.code_locations
    assert onboarding.code_locations[0].path == "src/jobs/onboarding.py"

    sweeper = by_service["TokenSweeper"]
    assert sweeper.action == "DELETE"
    assert sweeper.table == "session_store"

    reporting = by_service["ReportingDashboard"]
    assert reporting.action == "SELECT"
    assert reporting.table == "audit_log"
    assert "account_id" in reporting.columns

    for action in result.actions:
        assert 0.3 <= action.confidence <= 1.0

    assert capture_processor.traces_started, "expected tracing to record a trace"
    assert capture_processor.traces_finished == capture_processor.traces_started

    agent_span_exports = [
        span
        for span in capture_processor.spans_finished
        if span.get("span_data", {}).get("type") == "agent"
    ]
    assert agent_span_exports, "agent span should be recorded"
    assert agent_span_exports[0]["span_data"]["name"] == agent.agent_name


def test_glossary_overrides_extend_defaults() -> None:
    """Glossary overrides should supplement the default alias set."""
    overrides = TermGlossary(table_aliases={"customer book": "customer_ledger"})
    agent = NormalizeAgent()
    requests = [
        {
            "description": "Finance service writes entries into the customer book when invoices settle.",
            "service": "FinanceSvc",
        },
    ]

    result = agent.normalize(requests, glossary_overrides=overrides)

    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.table == "customer_ledger"
    assert action.action == "INSERT"
    assert action.service == "FinanceSvc"
