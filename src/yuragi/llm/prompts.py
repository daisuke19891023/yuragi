"""Prompt builders for LLM-backed agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from yuragi.core.models import CRUDAction

if TYPE_CHECKING:
    from collections.abc import Sequence
    from yuragi.agents.normalize_agent import TermGlossary

_PROMPT_HEADER = (
    "Normalize ambiguous CRUD descriptions. Use the glossary to map alias terms "
    "to canonical identifiers and prefer deterministic outputs."
)


@dataclass(frozen=True)
class NormalizationFewShot:
    """Representation of a single normalisation example used for prompting."""

    description: str
    action: CRUDAction

    def summary(self) -> str:
        """Return a deterministic summary of the example for prompt construction."""
        return f"{self.action.action} on {self.action.table} (service {self.action.service})"


def default_normalization_few_shots() -> list[NormalizationFewShot]:
    """Provide illustrative examples embedded into the agent instructions."""
    return [
        NormalizationFewShot(
            description="Order API persists checkout totals into billing_ledger",
            action=CRUDAction(
                service="OrderAPI",
                table="billing_ledger",
                action="INSERT",
                columns=["checkout_total"],
                where_keys=["order_id"],
                code_locations=[],
                confidence=0.8,
            ),
        ),
        NormalizationFewShot(
            description="Nightly vacuum prunes stale sessions from session_store",
            action=CRUDAction(
                service="SessionSweeper",
                table="session_store",
                action="DELETE",
                columns=[],
                where_keys=["last_seen_at"],
                code_locations=[],
                confidence=0.7,
            ),
        ),
    ]


def format_normalization_glossary(glossary: TermGlossary) -> str:
    """Generate a deterministic text block documenting glossary aliases."""
    lines: list[str] = ["- services:"]
    for alias, canonical in sorted(glossary.service_aliases.items()):
        lines.append(f"  - '{alias}' -> '{canonical}'")

    lines.append("- tables:")
    for alias, canonical in sorted(glossary.table_aliases.items()):
        lines.append(f"  - '{alias}' -> '{canonical}'")

    lines.append("- columns:")
    for alias, canonical in sorted(glossary.column_aliases.items()):
        lines.append(f"  - '{alias}' -> '{canonical}'")

    return "\n".join(lines)


def format_normalization_few_shots(few_shots: Sequence[NormalizationFewShot]) -> str:
    """Generate a deterministic representation of the few-shot examples."""
    lines: list[str] = []
    for shot in few_shots:
        lines.append(f"- input: {shot.description}")
        lines.append(f"  output: {shot.summary()}")
    return "\n".join(lines)


def build_normalization_system_prompt(
    glossary: TermGlossary,
    few_shots: Sequence[NormalizationFewShot],
) -> str:
    """Assemble the system prompt for the normalization agent."""
    glossary_block = format_normalization_glossary(glossary)
    few_shot_block = format_normalization_few_shots(few_shots)
    sections = [
        _PROMPT_HEADER,
        glossary_block,
    ]
    if few_shot_block:
        sections.append("Few-shot normalisations:\n" + few_shot_block)
    else:
        sections.append("Few-shot normalisations:")
    return "\n".join(sections)
