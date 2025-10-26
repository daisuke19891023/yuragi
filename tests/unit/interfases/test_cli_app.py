from __future__ import annotations

import json
from pathlib import Path

import pytest

from yuragi.interfases.cli.app import main as cli_main


@pytest.fixture(scope="module")
def samples_dir() -> Path:
    """Return the directory containing CLI sample fixtures."""
    return Path(__file__).resolve().parents[3] / "samples"


def test_schema_export_writes_file(tmp_path: Path) -> None:
    """The schema export command should create a schema file."""
    output = tmp_path / "schema.json"
    exit_code = cli_main(["schema", "export", "--out", str(output)])
    assert exit_code == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["$schema"].endswith("2020-12/schema")
    assert payload["title"] == "YuragiGraph"
    assert payload["$id"].endswith("graph.json")


def test_normalize_outputs_actions(samples_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Normalization should emit CRUD actions for the sample requests."""
    requests_path = samples_dir / "crud_requests.json"

    exit_code = cli_main(["normalize", "--input", str(requests_path)])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert not captured.err
    payload = json.loads(captured.out)
    assert payload["actions"], "Expected at least one normalized action"
    action = payload["actions"][0]
    assert action["service"] == "BillingService"
    assert action["table"] == "billing_ledger"


def test_pipeline_generates_graph(
    samples_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Pipeline command should serialize a verified graph and echo it to stdout."""
    graph_path = tmp_path / "graph.json"
    exit_code = cli_main(
        [
            "run-crud-pipeline",
            "--requests",
            str(samples_dir / "crud_requests.json"),
            "--repo-fixture",
            str(samples_dir / "repo_hits.json"),
            "--db-fixture",
            str(samples_dir / "db_fixture.json"),
            "--schema",
            "public",
            "--default-service",
            "BillingService",
            "--out",
            str(graph_path),
        ],
    )
    assert exit_code == 0

    captured = capsys.readouterr()
    assert not captured.err

    stdout_graph = json.loads(captured.out)
    file_graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert stdout_graph == file_graph
    assert stdout_graph["edges"], "Graph should include at least one edge"
    edge = stdout_graph["edges"][0]
    assert edge["type"] == "WRITES"
    assert edge["confidence"] >= 0.7


def test_pipeline_missing_requests_file_returns_error(
    samples_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing request files should result in a JSON error payload."""
    missing = tmp_path / "missing.json"
    exit_code = cli_main(
        [
            "run-crud-pipeline",
            "--requests",
            str(missing),
            "--repo-fixture",
            str(samples_dir / "repo_hits.json"),
            "--db-fixture",
            str(samples_dir / "db_fixture.json"),
        ],
    )
    assert exit_code == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    error_payload = json.loads(captured.err)
    assert error_payload["status"] == "error"
    assert "File not found" in error_payload["message"]
