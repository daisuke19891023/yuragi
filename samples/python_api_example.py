"""End-to-end example that exercises the yuragi Python API."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rich.console import Console

try:
    from yuragi.agents import (
        CrudWorkflowOrchestrator,
        NormalizeAgent,
        NormalizationRequest,
        TermGlossary,
        VerifyAgent,
    )
    from yuragi.interfases.mcp.server_fastmcp import (
        DatabaseFixturePayload,
        DatabaseOptions,
        RepoFixturePayload,
        RepoOptions,
    )
    from yuragi.pipelines import (
        CrudNormalizationPipeline,
        PipelineOutput,
        PipelineOutputFormat,
    )
except ModuleNotFoundError as error:  # pragma: no cover - documentation helper
    if "yuragi" not in (error.name or ""):
        raise
    project_root = Path(__file__).resolve().parents[1]
    src_path = project_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    from yuragi.agents import (
        CrudWorkflowOrchestrator,
        NormalizeAgent,
        NormalizationRequest,
        TermGlossary,
        VerifyAgent,
    )
    from yuragi.interfases.mcp.server_fastmcp import (
        DatabaseFixturePayload,
        DatabaseOptions,
        RepoFixturePayload,
        RepoOptions,
    )
    from yuragi.pipelines import (
        CrudNormalizationPipeline,
        PipelineOutput,
        PipelineOutputFormat,
    )


def _load_requests(path: Path) -> list[NormalizationRequest]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        NormalizationRequest(
            description=item["description"],
            service=item.get("service"),
            table_hint=item.get("table_hint"),
            columns_hint=tuple(item.get("columns_hint", [])),
            where_hint=tuple(item.get("where_hint", [])),
            path=item.get("path"),
            span=item.get("span"),
        )
        for item in data
    ]


def _load_glossary(path: Path) -> TermGlossary:
    data = json.loads(path.read_text(encoding="utf-8"))
    return TermGlossary(
        service_aliases=data.get("service_aliases", {}),
        table_aliases=data.get("table_aliases", {}),
        column_aliases=data.get("column_aliases", {}),
    )


def main() -> None:
    """Generate a verified dependency graph using bundled fixtures."""
    console = Console()
    base_dir = Path(__file__).resolve().parent
    requests_path = base_dir / "crud_requests.json"
    repo_fixture_path = base_dir / "repo_hits.json"
    db_fixture_path = base_dir / "db_fixture.json"
    glossary_path = base_dir / "glossary.json"

    requests = _load_requests(requests_path)
    glossary = _load_glossary(glossary_path)

    repo_fixture_raw = json.loads(repo_fixture_path.read_text(encoding="utf-8"))
    if "hits" in repo_fixture_raw:
        repo_fixture = RepoFixturePayload.model_validate(repo_fixture_raw)
    else:
        repo_fixture = RepoFixturePayload.model_validate({"hits": repo_fixture_raw})
    repo_options = RepoOptions(fixture=repo_fixture)
    repository, base_query = repo_options.build()

    db_fixture = DatabaseFixturePayload.model_validate_json(
        db_fixture_path.read_text(encoding="utf-8"),
    )
    db_options = DatabaseOptions(fixture=db_fixture, schema="public")
    database, schema = db_options.build()

    orchestrator = CrudWorkflowOrchestrator(
        normalize_agent=NormalizeAgent(),
        verify_agent=VerifyAgent(repository=repository, database=database),
    )

    pipeline = CrudNormalizationPipeline(orchestrator=orchestrator)
    output_path = Path("graph_python.json")
    graph = pipeline.run(
        requests,
        default_service="BillingService",
        glossary_overrides=glossary,
        repo_base_query=base_query,
        schema=schema,
        outputs=[
            PipelineOutput(
                format=PipelineOutputFormat.JSON,
                path=output_path,
            ),
        ],
    )

    console.print(f"Graph written to [path]{output_path.resolve()}[/path]")
    console.print_json(graph.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
