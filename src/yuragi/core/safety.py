"""Utilities for redacting sensitive data and guarding structured prompts."""

from __future__ import annotations

import re
import typing
from collections.abc import Mapping, Sequence, Set as AbstractSet
from typing import Any

_REDACTION_PLACEHOLDER = "[redacted]"
_ELLIPSIS = "\u2026"

_EMAIL_PATTERN = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_PHONE_PATTERN = re.compile(r"\b(?:\+?\d{1,3}[ \-]?)?(?:\d[ \-]?){7,}\d\b")
_LONG_DIGIT_PATTERN = re.compile(r"\b\d{9,}\b")
_TOKEN_PATTERN = re.compile(r"\b[A-Z0-9]{24,}\b")


def mask_pii(text: str, *, max_length: int = 512) -> str:
    """Redact common PII patterns from *text* and enforce a length ceiling."""
    masked = _EMAIL_PATTERN.sub(_REDACTION_PLACEHOLDER, text)
    masked = _PHONE_PATTERN.sub(_REDACTION_PLACEHOLDER, masked)
    masked = _LONG_DIGIT_PATTERN.sub(_REDACTION_PLACEHOLDER, masked)
    masked = _TOKEN_PATTERN.sub(_REDACTION_PLACEHOLDER, masked)

    if max_length > 0 and len(masked) > max_length:
        return masked[:max_length] + _ELLIPSIS
    return masked


def scrub_for_logging(value: Any, *, max_length: int = 512) -> Any:
    """Return a structure safe for logging by masking nested string values."""
    if isinstance(value, str):
        processed: Any = mask_pii(value, max_length=max_length)
    elif isinstance(value, bytes):
        processed = mask_pii(value.decode("utf-8", errors="ignore"), max_length=max_length)
    elif isinstance(value, Mapping):
        processed_mapping: dict[Any, Any] = {}
        mapping_items = typing.cast("Mapping[Any, Any]", value)
        for key, item in mapping_items.items():
            processed_mapping[key] = scrub_for_logging(item, max_length=max_length)
        processed = processed_mapping
    elif isinstance(value, list):
        list_items = typing.cast("list[Any]", value)
        processed = [scrub_for_logging(item, max_length=max_length) for item in list_items]
    elif isinstance(value, tuple):
        tuple_items = typing.cast("tuple[Any, ...]", value)
        processed = tuple(
            scrub_for_logging(item, max_length=max_length) for item in tuple_items
        )
    elif isinstance(value, AbstractSet):
        set_items = typing.cast("AbstractSet[Any]", value)
        processed = {scrub_for_logging(item, max_length=max_length) for item in set_items}
    elif isinstance(value, Sequence):
        sequence_items = typing.cast("Sequence[Any]", value)
        processed = [
            scrub_for_logging(item, max_length=max_length) for item in sequence_items
        ]
    else:
        processed = value
    return processed


GUARD_SYSTEM_MESSAGE = {
    "role": "system",
    "content": (
        "Respond using JSON only. Natural language prose or free-form commentary "
        "is prohibited. Ensure the reply strictly conforms to the provided schema."
    ),
}


__all__ = ["GUARD_SYSTEM_MESSAGE", "mask_pii", "scrub_for_logging"]
