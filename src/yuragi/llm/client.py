"""OpenAI client wrapper with retries, timeouts, and logging."""

from __future__ import annotations

import logging
import typing
from typing import Any, Protocol, TypeVar

from openai import OpenAI
from tenacity import Retrying, RetryError, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

_T = TypeVar("_T")


class ResponsesEndpoint(Protocol):
    """Protocol describing the OpenAI ``responses`` endpoint."""

    def create(self, *, model: str, timeout: float, **kwargs: Any) -> Any:
        """Create a response using the configured LLM model."""


class OpenAIClientProtocol(Protocol):
    """Protocol for the OpenAI client surface used by :class:`LLMClient`."""

    responses: ResponsesEndpoint


class LLMClientError(RuntimeError):
    """Base exception raised by :class:`LLMClient`."""


class RetryableLLMError(LLMClientError):
    """An error that should trigger an automatic retry."""


class LLMMaxRetriesExceededError(LLMClientError):
    """Raised when the client exhausts the configured retry attempts."""


class LLMClient:
    """Wrapper around the OpenAI client with retries, logging, and timeouts."""

    def __init__(
        self,
        *,
        client: OpenAIClientProtocol | None = None,
        model: str = "gpt-4.1-mini",
        timeout: float = 30.0,
        max_retries: int = 3,
        logger: logging.Logger | None = None,
        wait_initial: float = 0.5,
        wait_max: float = 4.0,
    ) -> None:
        """Initialise the wrapper with retry and timeout behaviour."""
        if max_retries < 1:
            error_message = "max_retries must be at least 1"
            raise ValueError(error_message)

        if client is None:
            self._client = typing.cast("OpenAIClientProtocol", OpenAI())
        else:
            self._client = client
        self._default_model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._wait_initial = wait_initial
        self._wait_max = wait_max
        self._logger = logger or logging.getLogger(__name__)

    @property
    def logger(self) -> logging.Logger:
        """Return the logger instance used by the client."""
        return self._logger

    def _build_retrying(self) -> Retrying:
        """Construct a Tenacity retrying helper for the configured settings."""
        return Retrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential_jitter(initial=self._wait_initial, max=self._wait_max),
            retry=retry_if_exception_type(RetryableLLMError),
            reraise=False,
        )

    def run_with_retry(self, func: typing.Callable[[], _T]) -> _T:
        """Execute *func* with the configured retry strategy."""
        retrying = self._build_retrying()

        try:
            return retrying(func)
        except RetryError as exc:
            last_exception = exc.last_attempt.exception()
            if isinstance(last_exception, RetryableLLMError):
                message = f"LLM request failed after {self._max_retries} attempts"
                raise LLMMaxRetriesExceededError(message) from last_exception
            message = "LLM request failed with an unexpected error"
            raise LLMMaxRetriesExceededError(message) from exc

    def create_response(self, *, model: str | None = None, timeout: float | None = None, **kwargs: Any) -> Any:
        """Call ``responses.create`` with retries and logging."""
        model_name = model or self._default_model
        effective_timeout = timeout or self._timeout

        def _invoke() -> Any:
            self._logger.debug(
                "Dispatching LLM request", extra={"model": model_name, "timeout": effective_timeout},
            )
            try:
                response: Any = self._client.responses.create(
                    model=model_name,
                    timeout=effective_timeout,
                    **kwargs,
                )
            except Exception as exc:
                self._logger.warning("LLM request failed", exc_info=exc)
                error_message = "Failed to call OpenAI responses endpoint"
                raise RetryableLLMError(error_message) from exc

            self._log_usage(response)
            return response

        return self.run_with_retry(_invoke)

    def _log_usage(self, response: Any) -> None:
        """Log basic token usage information if available."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return

        input_tokens = _safe_getattr(usage, "input_tokens")
        output_tokens = _safe_getattr(usage, "output_tokens")
        total_tokens = _safe_getattr(usage, "total_tokens")
        usage_payload = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
        self._logger.info("LLM usage", extra=usage_payload)

    @staticmethod
    def extract_output_text(response: Any) -> str:
        """Return the textual output from a Responses API result."""
        text = getattr(response, "output_text", None)
        if text:
            return text

        output = getattr(response, "output", None)
        if output is None:
            error_message = "LLM response did not contain textual output"
            raise LLMClientError(error_message)

        collected: list[str] = []
        for block in output:
            content_items = getattr(block, "content", None)
            if content_items is None:
                continue
            for item in content_items:
                item_text = getattr(item, "text", None)
                if item_text:
                    collected.append(item_text)
                json_payload = getattr(item, "json", None)
                if json_payload:
                    collected.append(str(json_payload))
        if not collected:
            error_message = "LLM response did not contain textual output"
            raise LLMClientError(error_message)
        return "".join(collected)


def _safe_getattr(obj: Any, name: str) -> Any:
    """Return ``getattr`` value while supporting mapping-like access."""
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        typed_obj = typing.cast("dict[str, Any]", obj)
        return typed_obj.get(name)
    return None


__all__ = [
    "LLMClient",
    "LLMClientError",
    "LLMMaxRetriesExceededError",
    "RetryableLLMError",
]
