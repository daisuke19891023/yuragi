"""Domain-specific exception hierarchy for yuragi."""

from __future__ import annotations


class YuragiError(Exception):
    """Base class for all domain-specific errors raised by yuragi."""


class YuragiValidationError(YuragiError):
    """Raised when inputs, configuration, or payloads fail validation rules."""


class GraphValidationError(YuragiValidationError):
    """Raised when a dependency graph fails structural or evidence validation."""


class AgentError(YuragiError):
    """Base class for issues surfaced by agent orchestration or execution."""


class OrchestrationError(AgentError):
    """Raised when the workflow orchestrator cannot recover from agent failures."""


class LLMClientError(YuragiError):
    """Raised when the LLM client fails to obtain or validate a structured response."""


class ExposureError(YuragiError):
    """Base class for errors surfaced through CLI or MCP exposures."""


class ExposureConfigurationError(ExposureError, YuragiValidationError):
    """Raised when an exposure receives invalid configuration or options."""


class ExposureStateError(ExposureError):
    """Raised when an exposure is invoked while it is in an invalid state."""


__all__ = [
    "AgentError",
    "ExposureConfigurationError",
    "ExposureError",
    "ExposureStateError",
    "GraphValidationError",
    "LLMClientError",
    "OrchestrationError",
    "YuragiError",
    "YuragiValidationError",
]
