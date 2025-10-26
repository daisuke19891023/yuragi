"""High-level pipeline for CRUD normalization and verification."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from jsonschema import Draft202012Validator

from yuragi.core.errors import GraphValidationError
from yuragi.core.models import Edge, Graph
from yuragi.core.schema import build_graph_json_schema

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from yuragi.agents import CrudWorkflowOrchestrator, NormalizationRequest, TermGlossary
    from yuragi.tools.repo import SearchQuery


class PipelineOutputFormat(StrEnum):
    """Supported serialization formats for pipeline outputs."""

    JSON = "json"
    NDJSON = "ndjson"


@dataclass(frozen=True)
class PipelineOutput:
    """Configuration for writing the pipeline result to disk."""

    format: PipelineOutputFormat
    path: Path

    def __post_init__(self) -> None:
        """Ensure *path* is stored as a :class:`Path` instance."""
        object.__setattr__(self, "path", Path(self.path))


@dataclass
class CrudNormalizationPipeline:
    """Execute the CRUD normalization workflow and emit validated graphs."""

    orchestrator: CrudWorkflowOrchestrator
    validator: Draft202012Validator = field(
        default_factory=lambda: Draft202012Validator(build_graph_json_schema()),
    )

    def run(
        self,
        requests: Sequence[NormalizationRequest | Mapping[str, object]],
        *,
        default_service: str | None = None,
        glossary_overrides: TermGlossary | None = None,
        repo_base_query: SearchQuery | None = None,
        schema: str | None = None,
        outputs: Sequence[PipelineOutput] | None = None,
    ) -> Graph:
        """Run the orchestrator, validate the graph, and persist optional outputs."""
        graph = self.orchestrator.run(
            requests,
            default_service=default_service,
            glossary_overrides=glossary_overrides,
            repo_base_query=repo_base_query,
            schema=schema,
        )

        if not graph.edges:
            return graph

        self._validate_graph(graph)
        prepared = self._prepare_graph(graph)

        for target in outputs or ():
            if target.format is PipelineOutputFormat.JSON:
                self._write_json(prepared, target.path)
            elif target.format is PipelineOutputFormat.NDJSON:
                self._write_ndjson(prepared, target.path)
            else:  # pragma: no cover - defensive future-proofing
                message = f"Unsupported output format: {target.format}"
                raise GraphValidationError(message)

        return prepared

    def _validate_graph(self, graph: Graph) -> None:
        payload: dict[str, Any] = graph.model_dump(mode="json")
        validator = cast("Any", self.validator)
        validator.validate(payload)

        missing_evidence: list[Edge] = [edge for edge in graph.edges if not edge.evidence]
        if missing_evidence:
            example = missing_evidence[0]
            message = (
                "Graph edge is missing evidence: "
                f"{example.from_id} -> {example.to_id} ({example.type})"
            )
            raise GraphValidationError(message)

    def _prepare_graph(self, graph: Graph) -> Graph:
        nodes = [node.model_copy(deep=True) for node in graph.nodes]
        nodes.sort(key=lambda node: node.id)

        edges = [edge.model_copy(deep=True) for edge in graph.edges]
        for edge in edges:
            edge.evidence.sort(key=lambda ev: (ev.type, ev.locator))
        edges.sort(key=lambda edge: (edge.from_id, edge.to_id, edge.type, edge.confidence))

        return Graph(nodes=nodes, edges=edges, schema_version=graph.schema_version)

    def _write_json(self, graph: Graph, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = graph.model_dump(mode="json")
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        path.write_text(serialized + "\n", encoding="utf-8")

    def _write_ndjson(self, graph: Graph, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = list(self._iter_ndjson_entries(graph))
        if not entries:
            path.write_text("", encoding="utf-8")
            return
        lines = [json.dumps(entry, ensure_ascii=False, sort_keys=True) for entry in entries]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _iter_ndjson_entries(self, graph: Graph) -> Iterator[dict[str, object]]:
        yield {
            "record_type": "graph",
            "graph": {
                "schema_version": graph.schema_version,
                "node_count": len(graph.nodes),
                "edge_count": len(graph.edges),
            },
        }
        for node in graph.nodes:
            yield {
                "record_type": "node",
                "node": node.model_dump(mode="json"),
            }
        for edge in graph.edges:
            yield {
                "record_type": "edge",
                "edge": edge.model_dump(mode="json"),
            }


__all__ = ["CrudNormalizationPipeline", "PipelineOutput", "PipelineOutputFormat"]
