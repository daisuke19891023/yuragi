"""Utilities for enforcing structured outputs from the LLM."""

from __future__ import annotations

import typing
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from .client import LLMClient, LLMClientError, LLMMaxRetriesExceededError, RetryableLLMError
from yuragi.core.safety import GUARD_SYSTEM_MESSAGE, mask_pii


class StructuredOutputError(LLMClientError):
    """Raised when an LLM response cannot be parsed into the target schema."""

    def __init__(self, message: str, *, raw_response: str, validation_error: ValidationError) -> None:
        """Store details about the failed structured output."""
        super().__init__(message)
        self.raw_response = mask_pii(raw_response)
        self.validation_error = validation_error


class StructuredOutputValidationError(RetryableLLMError):
    """Retryable error indicating that the response violated the schema."""

    def __init__(self, raw_response: str, validation_error: ValidationError) -> None:
        """Capture the raw response and associated validation error."""
        super().__init__("LLM response failed schema validation")
        self.raw_response = mask_pii(raw_response)
        self.validation_error = validation_error


def build_json_schema_response_format(model: type[BaseModel], *, name: str | None = None) -> dict[str, Any]:
    """Return the OpenAI ``response_format`` payload for *model*."""
    schema_name = name or model.__name__
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": model.model_json_schema(),
            "strict": True,
        },
    }


class StructuredOutputGenerator[T: BaseModel]:
    """Request helper that enforces strict structured output for a Pydantic model."""

    def __init__(self, client: LLMClient, output_model: type[T], *, schema_name: str | None = None) -> None:
        """Configure the generator for a specific model and JSON schema name."""
        self._client = client
        self._output_model = output_model
        self._response_format = build_json_schema_response_format(output_model, name=schema_name)

    def generate(
        self,
        *,
        prompt: typing.Sequence[dict[str, Any]] | str,
        model: str | None = None,
        **kwargs: Any,
    ) -> T:
        """Request a structured response and validate against ``output_model``."""
        last_validation_error: StructuredOutputValidationError | None = None

        def _invoke() -> T:
            guarded_input = _prepare_prompt_input(prompt)
            response = self._client.create_response(
                model=model,
                response_format=self._response_format,
                input=guarded_input,
                **kwargs,
            )
            raw_text = self._client.extract_output_text(response)
            try:
                return self._output_model.model_validate_json(raw_text)
            except ValidationError as exc:
                raise StructuredOutputValidationError(raw_text, exc) from exc

        try:
            return self._client.run_with_retry(_invoke)
        except LLMMaxRetriesExceededError as exc:
            cause = exc.__cause__
            if isinstance(cause, StructuredOutputValidationError):
                last_validation_error = cause
            if last_validation_error is None:
                raise
            message = "LLM response did not conform to the expected schema"
            raise StructuredOutputError(
                message,
                raw_response=last_validation_error.raw_response,
                validation_error=last_validation_error.validation_error,
            ) from last_validation_error


def _prepare_prompt_input(prompt: Sequence[dict[str, Any]] | str) -> list[dict[str, Any]]:
    """Return a message sequence prefixed with the JSON-only guard rail."""
    if isinstance(prompt, str):
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    else:
        messages = []
        candidate_sequence = typing.cast(Sequence[Any], prompt)
        for index, item in enumerate(candidate_sequence):
            if not isinstance(item, Mapping):
                message = (
                    "Prompt sequences must contain mapping objects, "
                    f"encountered {type(item)!r} at index {index}"
                )
                raise TypeError(message)
            mapping_item = typing.cast(Mapping[str, Any], item)
            messages.append(dict(mapping_item))

    if messages:
        first = messages[0]
        if (
            first.get("role") == GUARD_SYSTEM_MESSAGE["role"]
            and first.get("content") == GUARD_SYSTEM_MESSAGE["content"]
        ):
            return messages

    return [dict(GUARD_SYSTEM_MESSAGE), *messages]


__all__ = [
    "StructuredOutputError",
    "StructuredOutputGenerator",
    "StructuredOutputValidationError",
    "build_json_schema_response_format",
]
