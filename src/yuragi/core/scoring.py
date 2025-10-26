"""Confidence scoring rules for evidence-backed edges."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

from .models import Evidence, EvidenceType

STATIC_EVIDENCE_TYPES: frozenset[EvidenceType] = frozenset(
    {EvidenceType.CODE, EvidenceType.SPEC, EvidenceType.CONFIG},
)
"""Evidence categories considered static analysis."""

RUNTIME_EVIDENCE_TYPES: frozenset[EvidenceType] = frozenset(
    {EvidenceType.LOG, EvidenceType.TRACE},
)
"""Evidence categories considered runtime validation."""

STATIC_BONUS = 0.3
RUNTIME_BONUS = 0.3
MULTI_TOOL_BONUS = 0.2
NAME_COLLISION_PENALTY = -0.2
CONFIRMED_THRESHOLD = 0.7
MULTI_TOOL_AGREEMENT_THRESHOLD = 2


@dataclass(frozen=True)
class ConfidenceContribution:
    """A single contribution that affected the confidence score."""

    reason: str
    delta: float


@dataclass(frozen=True)
class ConfidenceResult:
    """Computed confidence score with supporting breakdown."""

    confidence: float
    confirmed: bool
    contributions: tuple[ConfidenceContribution, ...]


def _has_static_evidence(evidence: Iterable[Evidence]) -> bool:
    return any(item.type in STATIC_EVIDENCE_TYPES for item in evidence)


def _has_runtime_evidence(evidence: Iterable[Evidence]) -> bool:
    return any(item.type in RUNTIME_EVIDENCE_TYPES for item in evidence)


def _has_multi_tool_agreement(evidence: Iterable[Evidence]) -> bool:
    tools = {item.source_tool for item in evidence if item.source_tool}
    return len(tools) >= MULTI_TOOL_AGREEMENT_THRESHOLD


def calculate_confidence(
    evidence: Sequence[Evidence], *, has_name_collision: bool = False,
) -> ConfidenceResult:
    """Calculate the confidence score for an edge based on evidence inputs."""
    contributions: list[ConfidenceContribution] = []
    score = 0.0

    if _has_static_evidence(evidence):
        contributions.append(
            ConfidenceContribution("static-analysis-evidence", STATIC_BONUS),
        )
        score += STATIC_BONUS

    if _has_runtime_evidence(evidence):
        contributions.append(
            ConfidenceContribution("runtime-evidence", RUNTIME_BONUS),
        )
        score += RUNTIME_BONUS

    if _has_multi_tool_agreement(evidence):
        contributions.append(
            ConfidenceContribution("multi-tool-agreement", MULTI_TOOL_BONUS),
        )
        score += MULTI_TOOL_BONUS

    if has_name_collision:
        contributions.append(
            ConfidenceContribution("name-collision", NAME_COLLISION_PENALTY),
        )
        score += NAME_COLLISION_PENALTY

    score = max(0.0, min(1.0, score))
    confirmed = score >= CONFIRMED_THRESHOLD

    return ConfidenceResult(score, confirmed, tuple(contributions))


__all__ = [
    "CONFIRMED_THRESHOLD",
    "MULTI_TOOL_AGREEMENT_THRESHOLD",
    "RUNTIME_EVIDENCE_TYPES",
    "STATIC_EVIDENCE_TYPES",
    "ConfidenceContribution",
    "ConfidenceResult",
    "calculate_confidence",
]
