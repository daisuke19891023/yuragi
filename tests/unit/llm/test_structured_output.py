"""Tests for the structured LLM client helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
import typing
from typing import TYPE_CHECKING, Any

import pytest

from yuragi.core.models import CRUDAction
from yuragi.llm import LLMClient, StructuredOutputError, StructuredOutputGenerator

if TYPE_CHECKING:
    from yuragi.llm.client import OpenAIClientProtocol


@dataclass
class FakeUsage:
    """Usage payload mimicking the OpenAI client."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


class FakeResponse:
    """Minimal response object exposing ``output_text`` and ``usage``."""

    def __init__(self, payload: str, *, usage: FakeUsage | None = None) -> None:
        """Initialise the stub response with payload text and optional usage."""
        self.output_text = payload
        self.usage = usage


class _StubResponsesEndpoint:
    """Capture calls to ``responses.create`` while replaying canned responses."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        """Store the list of responses that will be returned sequentially."""
        self._responses = responses
        self.calls: list[dict[str, Any]] = []
        self._index = 0

    def create(self, *, model: str, timeout: float, **kwargs: Any) -> FakeResponse:
        """Return the next canned response and record the call arguments."""
        call_args = {"model": model, "timeout": timeout, **kwargs}
        self.calls.append(call_args)
        if self._index >= len(self._responses):  # pragma: no cover - guard rail
            raise AssertionError("Unexpected additional LLM call")
        response = self._responses[self._index]
        self._index += 1
        return response

    @property
    def call_count(self) -> int:
        """Return how many times ``create`` has been called."""
        return len(self.calls)


class StubOpenAI:
    """Stub OpenAI client exposing the ``responses.create`` interface."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        """Initialise the stub with a deterministic response sequence."""
        self.responses = _StubResponsesEndpoint(responses)


def _make_valid_payload(*, service: str, table: str, path: str) -> str:
    payload = {
        "service": service,
        "table": table,
        "action": "SELECT",
        "columns": ["id", "status", "total"],
        "where_keys": ["id"],
        "code_locations": [{"path": path, "span": "L10-L40"}],
        "confidence": 0.82,
    }
    return json.dumps(payload)


@pytest.mark.parametrize(
    "payload",
    [
        _make_valid_payload(service="orders-http", table="orders", path="src/services/orders/sql.py"),
        _make_valid_payload(service="billing-worker", table="payments", path="src/repos/billing/repository.py"),
        _make_valid_payload(service="report-renderer", table="reports", path="templates/report.sql.jinja"),
    ],
    ids=["string-sql", "orm", "templated"],
)
def test_structured_generator_parses_recordings(payload: str) -> None:
    """Recorded structured outputs are parsed into ``CRUDAction`` objects."""
    stub = StubOpenAI([FakeResponse(payload)])
    typed_client = typing.cast("OpenAIClientProtocol", stub)
    client = LLMClient(client=typed_client, max_retries=1)
    generator = StructuredOutputGenerator(client, CRUDAction)

    result = generator.generate(prompt=[{"role": "user", "content": "normalize"}])

    assert isinstance(result, CRUDAction)
    assert result.columns == ["id", "status", "total"]
    assert result.where_keys == ["id"]

    request_kwargs = stub.responses.calls[0]
    response_format = request_kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_structured_generator_retries_until_schema_match() -> None:
    """Schema violations trigger a retry before returning a valid payload."""
    valid_payload = _make_valid_payload(
        service="catalog-api", table="products", path="src/catalog/dao.py",
    )
    responses = [
        FakeResponse(json.dumps({"service": "catalog-api"})),
        FakeResponse(valid_payload),
    ]
    stub = StubOpenAI(responses)
    typed_client = typing.cast("OpenAIClientProtocol", stub)
    client = LLMClient(client=typed_client, max_retries=2)
    generator = StructuredOutputGenerator(client, CRUDAction)

    result = generator.generate(prompt="normalize")

    assert result.table == "products"
    assert stub.responses.call_count == 2


def test_structured_generator_raises_after_exhausting_retries() -> None:
    """Exhausting retries raises a ``StructuredOutputError`` with context."""
    responses = [FakeResponse("{}"), FakeResponse("{}")]
    stub = StubOpenAI(responses)
    typed_client = typing.cast("OpenAIClientProtocol", stub)
    client = LLMClient(client=typed_client, max_retries=2)
    generator = StructuredOutputGenerator(client, CRUDAction)

    with pytest.raises(StructuredOutputError) as excinfo:
        generator.generate(prompt="normalize")

    error = excinfo.value
    assert "schema" in str(error).lower()
    assert error.raw_response == "{}"
