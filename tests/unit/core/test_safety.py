from __future__ import annotations

from yuragi.core.safety import mask_pii, scrub_for_logging


def test_mask_pii_redacts_emails_and_phone_numbers() -> None:
    """Common PII patterns should be replaced with a redaction token."""
    payload = "Contact alice@example.com or +1 555-123-4567 for details."
    masked = mask_pii(payload)
    expected_redaction = mask_pii("alice@example.com")

    assert "alice@example.com" not in masked
    assert "555-123-4567" not in masked
    assert expected_redaction in masked


def test_mask_pii_truncates_when_exceeding_limit() -> None:
    """The helper should avoid emitting excessively long log entries."""
    payload = "a" * 600
    masked = mask_pii(payload, max_length=100)

    assert len(masked) == 101  # 100 characters plus ellipsis
    assert masked.endswith("\u2026")


def test_scrub_for_logging_masks_nested_structures() -> None:
    """Nested containers should have their string values redacted."""
    payload = {
        "email": "bob@example.com",
        "notes": ["Phone: 555 111 2222", {"token": "ABCDEF0123456789ABCDEF01"}],
    }

    scrubbed = scrub_for_logging(payload)

    expected_email = mask_pii("bob@example.com")
    expected_phone = mask_pii("Phone: 555 111 2222")
    expected_token = mask_pii("ABCDEF0123456789ABCDEF01")

    assert scrubbed["email"] == expected_email
    assert scrubbed["notes"][0] == expected_phone
    assert scrubbed["notes"][1]["token"] == expected_token
