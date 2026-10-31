"""Tests for RO-Crate (Process Run Crate 0.6) export."""

from __future__ import annotations

import json
import sys

import pytest

from sciproof import cli
from sciproof.errors import ManifestError
from sciproof.export import (
    PROCESS_RUN_CRATE_PROFILE,
    RO_CRATE_CONTEXT,
    format_ro_crate,
    to_ro_crate,
)


def test_ro_crate_structure(project):
    project.capture(name="ro_test")
    crate = to_ro_crate(project.root / "ro_test.sciproof.json")

    assert crate["@context"] == RO_CRATE_CONTEXT
    graph = {entity["@id"]: entity for entity in crate["@graph"]}

    # Root Dataset
    root = graph["./"]
    assert root["@type"] == "Dataset"
    assert any(c["@id"] == PROCESS_RUN_CRATE_PROFILE for c in root["conformsTo"])
    assert root["mentions"] == [{"@id": "#run"}]

    # Metadata Descriptor
    descriptor = graph["ro-crate-metadata.json"]
    assert descriptor["@type"] == "CreativeWork"
    assert descriptor["about"] == {"@id": "./"}

    # Run Action
    action = graph["#run"]
    assert action["@type"] == "CreateAction"
    assert action["actionStatus"] == "http://schema.org/CompletedActionStatus"
    assert action["instrument"] == {"@id": "#tool"}

    # Tool
    tool = graph["#tool"]
    assert tool["@type"] == "SoftwareApplication"
    assert tool["name"] == "sciproof"

    # Inputs and Outputs present in graph
    for inp in ("data.json", "experiment.py"):
        assert inp in graph
        assert {"@id": inp} in action["object"]
        assert {"@id": inp} in root["hasPart"]

    for out in ("out/exact.txt", "out/result.json"):
        assert out in graph
        assert {"@id": out} in action["result"]
        assert {"@id": out} in root["hasPart"]


def test_ro_crate_cli_and_file_export(project, capsys, tmp_path):
    project.capture(name="exp")
    assert cli.main(["export", "exp.sciproof.json", "--format", "ro-crate"]) == cli.EXIT_OK
    out_text = capsys.readouterr().out
    data = json.loads(out_text)
    assert data["@context"] == RO_CRATE_CONTEXT

    target = tmp_path / "ro-crate-metadata.json"
    assert (
        cli.main(["export", "exp.sciproof.json", "--format", "ro-crate", "-o", str(target)])
        == cli.EXIT_OK
    )
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8"))["@graph"]


def test_ro_crate_failed_run(project):
    cli.main(
        [
            "capture",
            "--name",
            "fail_exp",
            "--allow-nonzero",
            "--input",
            "data.json",
            "--",
            sys.executable,
            "-c",
            "import sys; sys.exit(42)",
        ]
    )
    crate = to_ro_crate(project.root / "fail_exp.sciproof.json")
    graph = {entity["@id"]: entity for entity in crate["@graph"]}
    action = graph["#run"]
    assert action["actionStatus"] == "http://schema.org/FailedActionStatus"


def test_ro_crate_invalid_manifest(project):
    (project.root / "bad.sciproof.json").write_text("{}")
    with pytest.raises(ManifestError):
        to_ro_crate(project.root / "bad.sciproof.json")


def test_format_ro_crate_helper(project):
    project.capture()
    formatted = format_ro_crate(project.manifest)
    assert formatted.endswith("\n")
    assert json.loads(formatted)["@context"] == RO_CRATE_CONTEXT


def test_ro_crate_directory_inputs_and_outputs(project):
    (project.root / "inputs").mkdir()
    (project.root / "inputs" / "x.csv").write_bytes(b"1,2\n")
    script = (
        "import pathlib; d = pathlib.Path('results'); d.mkdir(exist_ok=True); "
        "(d / 'a.txt').write_text('alpha'); (d / 'sub').mkdir(exist_ok=True); "
        "(d / 'sub' / 'b.txt').write_text('beta')"
    )
    project.capture(
        name="dirs",
        argv=[sys.executable, "-c", script],
        inputs=["data.json", "inputs"],
        outputs=["results"],
        numeric_outputs=[],
    )
    crate = to_ro_crate(project.root / "dirs.sciproof.json")
    graph = {entity["@id"]: entity for entity in crate["@graph"]}
    action = graph["#run"]
    assert graph["inputs/"]["@type"] == "Dataset"
    assert graph["inputs/"]["@id"].endswith("/")
    assert graph["results/"]["@type"] == "Dataset"
    assert graph["results/"]["@id"].endswith("/")
    assert {"@id": "inputs/"} in action["object"]
    assert {"@id": "results/"} in action["result"]
