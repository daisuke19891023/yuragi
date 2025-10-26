"""Tests for the yuragi error hierarchy and integration points."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from yuragi.core.errors import (
    ExposureConfigurationError,
    ExposureError,
    ExposureStateError,
    GraphValidationError,
    OrchestrationError,
    YuragiError,
    YuragiValidationError,
)
from yuragi.core.models import Edge, EdgeType, Graph, Node, NodeType
from yuragi.interfases.cli.app import CliError
from yuragi.interfases.factory import make_exposure
from yuragi.interfases.mcp.server_fastmcp import DatabaseOptions, MCPExposure
from yuragi.pipelines import CrudNormalizationPipeline

if TYPE_CHECKING:
    from yuragi.agents import CrudWorkflowOrchestrator


def test_error_hierarchy() -> None:
    """Specialised errors should remain anchored to the YuragiError base."""
    assert issubclass(GraphValidationError, YuragiValidationError)
    assert issubclass(YuragiValidationError, YuragiError)
    assert issubclass(ExposureConfigurationError, ExposureError)
    assert issubclass(ExposureConfigurationError, YuragiValidationError)
    assert issubclass(OrchestrationError, YuragiError)


def test_pipeline_raises_graph_validation_error_for_missing_evidence() -> None:
    """Graph validation failures are surfaced as GraphValidationError."""

    class DummyOrchestrator:
        def run(self, *_: object, **__: object) -> Graph:  # pragma: no cover - simple stub
            node = Node(id="service:catalog", type=NodeType.SERVICE, name="catalog")
            edge = Edge(
                from_id=node.id,
                to_id=node.id,
                type=EdgeType.CALLS,
                confidence=0.5,
                evidence=[],
            )
            return Graph(nodes=[node], edges=[edge])

    orchestrator = DummyOrchestrator()
    pipeline = CrudNormalizationPipeline(
        orchestrator=cast("CrudWorkflowOrchestrator", orchestrator),
    )

    with pytest.raises(GraphValidationError):
        pipeline.run([])


def test_cli_error_inherits_exposure_error() -> None:
    """The CLI should surface anticipated failures via ExposureError subclasses."""
    error = CliError("boom", exit_code=2)
    assert isinstance(error, ExposureError)
    assert error.exit_code == 2


def test_database_options_reject_custom_configuration() -> None:
    """FastMCP database options should reject custom connection details."""
    options = DatabaseOptions(engine="postgres")
    with pytest.raises(ExposureConfigurationError):
        options.build()


def test_make_exposure_unknown_kind() -> None:
    """Unknown exposure kinds should surface a configuration error."""
    with pytest.raises(ExposureConfigurationError):
        make_exposure("unknown-kind")


def test_mcp_exposure_requires_runtime_before_serving() -> None:
    """Accessing the MCP runtime before serve() should raise ExposureStateError."""
    exposure = MCPExposure()
    require_runtime = object.__getattribute__(exposure, "_require_runtime")

    with pytest.raises(ExposureStateError):
        require_runtime()
