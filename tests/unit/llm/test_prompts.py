from __future__ import annotations

from yuragi.agents.normalize_agent import TermGlossary
from yuragi.core.models import CRUDAction
from yuragi.llm.prompts import (
    NormalizationFewShot,
    build_normalization_system_prompt,
    default_normalization_few_shots,
    format_normalization_few_shots,
    format_normalization_glossary,
)


def test_default_normalization_few_shots_are_deterministic() -> None:
    """Default few-shot examples should remain stable across invocations."""
    first = default_normalization_few_shots()
    second = default_normalization_few_shots()

    assert first == second
    text_block = format_normalization_few_shots(first)
    assert "Order API persists checkout totals into billing_ledger" in text_block
    assert "Nightly vacuum prunes stale sessions from session_store" in text_block


def test_glossary_formatting_orders_aliases() -> None:
    """Glossary aliases should be sorted to ensure deterministic prompts."""
    glossary = TermGlossary(
        service_aliases={"beta": "ServiceB", "alpha": "ServiceA"},
        table_aliases={"gamma": "tbl_gamma", "alpha": "tbl_alpha"},
        column_aliases={"epsilon": "col_eps", "delta": "col_delta"},
    )

    formatted = format_normalization_glossary(glossary)
    lines = formatted.splitlines()

    assert lines[0] == "- services:"
    assert lines[1] == "  - 'alpha' -> 'ServiceA'"
    assert lines[2] == "  - 'beta' -> 'ServiceB'"
    assert lines[3] == "- tables:"
    assert lines[4] == "  - 'alpha' -> 'tbl_alpha'"
    assert lines[5] == "  - 'gamma' -> 'tbl_gamma'"
    assert lines[6] == "- columns:"
    assert lines[7] == "  - 'delta' -> 'col_delta'"
    assert lines[8] == "  - 'epsilon' -> 'col_eps'"


def test_build_normalization_prompt_structure() -> None:
    """The full system prompt should include glossary and few-shot sections."""
    glossary = TermGlossary()
    few_shots = [
        NormalizationFewShot(
            description="Ingestion job writes audit entries",
            action=CRUDAction(
                service="Ingestion",
                table="audit_log",
                action="INSERT",
                columns=["payload"],
                where_keys=["event_id"],
                code_locations=[],
                confidence=0.9,
            ),
        ),
    ]

    prompt = build_normalization_system_prompt(glossary, few_shots)

    assert prompt.startswith(
        "Normalize ambiguous CRUD descriptions. Use the glossary to map alias terms",
    )
    assert "- services:" in prompt
    assert "- tables:" in prompt
    assert "- columns:" in prompt
    assert "Few-shot normalisations:" in prompt
    assert "- input: Ingestion job writes audit entries" in prompt
    assert "  output: INSERT on audit_log (service Ingestion)" in prompt
