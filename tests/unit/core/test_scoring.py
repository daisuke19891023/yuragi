"""Tests for the confidence scoring rules."""

from __future__ import annotations

import math

from yuragi.core import (
    CONFIRMED_THRESHOLD,
    Evidence,
    EvidenceType,
    calculate_confidence,
)


def make_evidence(
    *,
    evidence_type: EvidenceType,
    locator: str = "file.py:1",
    source_tool: str | None = None,
) -> Evidence:
    """Create a minimal evidence instance for scoring tests."""
    return Evidence(type=evidence_type, locator=locator, source_tool=source_tool)


def test_static_evidence_contribution() -> None:
    """Static evidence alone should contribute the static bonus."""
    evidence = [make_evidence(evidence_type=EvidenceType.CODE)]

    result = calculate_confidence(evidence)

    assert result.confidence == 0.3
    assert not result.confirmed
    assert any(
        contribution.reason == "static-analysis-evidence"
        for contribution in result.contributions
    )


def test_runtime_evidence_contribution() -> None:
    """Runtime evidence alone should contribute the runtime bonus."""
    evidence = [make_evidence(evidence_type=EvidenceType.LOG)]

    result = calculate_confidence(evidence)

    assert result.confidence == 0.3
    assert not result.confirmed
    assert any(
        contribution.reason == "runtime-evidence"
        for contribution in result.contributions
    )


def test_multi_tool_agreement_requires_distinct_tools() -> None:
    """Multiple evidences from the same tool should not trigger the agreement bonus."""
    evidence = [
        make_evidence(evidence_type=EvidenceType.CODE, source_tool="rg"),
        make_evidence(evidence_type=EvidenceType.CODE, source_tool="rg"),
    ]

    result = calculate_confidence(evidence)

    assert result.confidence == 0.3
    reasons = {contribution.reason for contribution in result.contributions}
    assert "multi-tool-agreement" not in reasons


def test_name_collision_penalty_applied() -> None:
    """Name collisions should deduct from the score even with static evidence."""
    evidence = [make_evidence(evidence_type=EvidenceType.SPEC)]

    result = calculate_confidence(evidence, has_name_collision=True)

    assert math.isclose(result.confidence, 0.1)
    reasons = {contribution.reason for contribution in result.contributions}
    assert "name-collision" in reasons


def test_confirmed_flag_threshold() -> None:
    """Static and runtime evidence from different tools should mark the edge confirmed."""
    evidence = [
        make_evidence(evidence_type=EvidenceType.CODE, source_tool="rg"),
        make_evidence(evidence_type=EvidenceType.LOG, source_tool="otel"),
    ]

    result = calculate_confidence(evidence)

    assert result.confidence == 0.8
    assert result.confirmed
    assert result.confidence >= CONFIRMED_THRESHOLD
