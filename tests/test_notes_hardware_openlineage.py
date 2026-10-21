"""Notes, the opt-in hardware record, and the OpenLineage export."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime

import pytest

from sciproof import cli, environment
from sciproof.diffmanifests import diff_manifests
from sciproof.environment import MAX_GPUS, hardware_info
from sciproof.errors import CaptureError, ManifestError
from sciproof.export import (
    OPENLINEAGE_PRODUCER,
    OPENLINEAGE_SCHEMA_URL,
    to_in_toto,
    to_openlineage,
)
from sciproof.inspect_manifest import describe
from sciproof.ledger import list_manifests
from sciproof.manifest import load_manifest, validate_manifest
from sciproof.verify import Status, verify

# ---------------------------------------------------------------- notes


def test_a_note_is_stored_and_shown(project):
    result = project.capture(note="baseline before the optimiser change")
    assert result.manifest["note"] == "baseline before the optimiser change"
    assert result.manifest["schema_version"] == "1.1"
    assert "Note:\n  baseline before the optimiser change" in describe(
        load_manifest(project.manifest)
    )
    assert list_manifests(project.root)[0].note == "baseline before the optimiser change"
    assert (
        to_in_toto(project.manifest)["predicate"]["note"] == "baseline before the optimiser change"
    )


def test_no_note_means_no_note_field_and_schema_1_0(project):
    result = project.capture()
    assert "note" not in result.manifest and result.manifest["schema_version"] == "1.0"


@pytest.mark.parametrize("note", ["", "   \n", "x" * 2001, "a\x00b"])
def test_unusable_notes_are_refused(project, note):
    with pytest.raises(CaptureError, match="the note must be"):
        project.capture(note=note)


def test_notes_are_text_only_and_escaped_when_shown(project):
    project.capture(note="line one\nline two \x1b[31mred")
    text = describe(load_manifest(project.manifest))
    assert "\x1b" not in text and "line two" in text


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(schema_version="1.0"), "needs schema_version 1.1"),
        (lambda d: d.update(note=5), "expected 1 to 2000 characters"),
        (lambda d: d.update(note=""), "expected 1 to 2000 characters"),
        (lambda d: d.update(note="x" * 2001), "expected 1 to 2000 characters"),
    ],
)
def test_invalid_notes_in_a_manifest_are_refused(project, mutate, message):
    project.capture(note="ok")
    data = json.loads(project.manifest.read_text())
    mutate(data)
    project.manifest.write_text(json.dumps(data))
    with pytest.raises(ManifestError, match=message):
        load_manifest(project.manifest)


def test_diff_shows_a_changed_added_and_removed_note(project):
    first = project.capture(name="a", note="first")
    second = project.capture(name="b", note="second")
    third = project.capture(name="c")
    (change,) = [
        c
        for c in diff_manifests(first.manifest_path, second.manifest_path).changes
        if c.section == "note"
    ]
    assert change.kind == "changed" and change.message == "first -> second"
    removed = next(
        c
        for c in diff_manifests(first.manifest_path, third.manifest_path).changes
        if c.section == "note"
    )
    assert removed.kind == "removed"
    added = next(
        c
        for c in diff_manifests(third.manifest_path, second.manifest_path).changes
        if c.section == "note"
    )
    assert added.kind == "added" and added.message == "second"


def test_the_cli_takes_a_note(project, capsys):
    code = ["capture", "--name", "n", "--note", "why", "--input", "data.json", "--"]
    assert cli.main([*code, sys.executable, "-c", "pass"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["inspect", "n.sciproof.json"]) == cli.EXIT_OK
    assert "Note:\n  why" in capsys.readouterr().out


# ---------------------------------------------------------------- hardware


class _Completed:
    def __init__(self, stdout):
        self.stdout = stdout


def _fake_nvidia(monkeypatch, output=None, error=None):
    monkeypatch.setattr(
        environment.shutil,
        "which",
        lambda name: "/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )

    def run(argv, **kwargs):
        assert argv[0] == "/bin/nvidia-smi" and kwargs["timeout"] == 10 and kwargs["check"] is True
        if error:
            raise error
        return _Completed(output)

    monkeypatch.setattr(environment.subprocess, "run", run)


def test_hardware_info_has_only_non_identifying_fields():
    info = hardware_info()
    assert set(info) == {"cpu_count", "processor", "gpu_probe", "gpus"}
    assert isinstance(info["cpu_count"], int) and info["cpu_count"] >= 1
    assert info["gpu_probe"] in ("nvidia-smi", "none", "failed")


def test_gpus_are_parsed_from_nvidia_smi(monkeypatch):
    _fake_nvidia(
        monkeypatch, "NVIDIA RTX 4090, 550.54.14, 24564\nTesla V100-SXM2, 535.104.05, 16384\n"
    )
    info = hardware_info()
    assert info["gpu_probe"] == "nvidia-smi"
    assert info["gpus"] == [
        {"name": "NVIDIA RTX 4090", "driver": "550.54.14", "memory_mib": 24564},
        {"name": "Tesla V100-SXM2", "driver": "535.104.05", "memory_mib": 16384},
    ]


def test_odd_nvidia_smi_output_is_handled(monkeypatch):
    _fake_nvidia(
        monkeypatch,
        "Card, With Comma, 1.0, 100\nnot enough fields\n, 1, 2\nNo Memory, 2.0, [N/A]\n",
    )
    gpus = hardware_info()["gpus"]
    assert gpus[0] == {"name": "Card,With Comma", "driver": "1.0", "memory_mib": 100}
    assert gpus[1] == {"name": "No Memory", "driver": "2.0", "memory_mib": None}
    assert len(gpus) == 2  # the line with too few fields and the one without a name are skipped


def test_at_most_a_fixed_number_of_gpus_are_recorded(monkeypatch):
    _fake_nvidia(monkeypatch, "".join(f"GPU{i}, 1, 1\n" for i in range(MAX_GPUS + 5)))
    assert len(hardware_info()["gpus"]) == MAX_GPUS


def test_a_failing_nvidia_smi_is_recorded_as_failed_not_as_no_gpu(monkeypatch):
    _fake_nvidia(monkeypatch, error=subprocess.CalledProcessError(9, "nvidia-smi"))
    info = hardware_info()
    assert info["gpu_probe"] == "failed" and info["gpus"] == []


def test_no_nvidia_smi_is_recorded_as_none(monkeypatch):
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    assert hardware_info()["gpu_probe"] == "none"


def test_hardware_is_only_recorded_on_request(project):
    assert "hardware" not in project.capture().manifest["environment"]
    result = project.capture(record_hardware=True, force=True)
    assert result.manifest["schema_version"] == "1.1"
    assert set(result.manifest["environment"]["hardware"]) == {
        "cpu_count",
        "processor",
        "gpu_probe",
        "gpus",
    }
    load_manifest(project.manifest)


def test_hardware_must_carry_cpu_count(project):
    """Readers index cpu_count directly, so the key must be present
    (issue #89): a manifest without it must be rejected, not crash inspect."""
    project.capture(record_hardware=True)
    manifest = json.loads(project.manifest.read_text())
    del manifest["environment"]["hardware"]["cpu_count"]
    with pytest.raises(ManifestError, match="cpu_count"):
        validate_manifest(manifest)


def test_cpu_count_may_be_null_when_the_probe_returned_none(project):
    """os.cpu_count() can return None, so a null value stays valid."""
    project.capture(record_hardware=True)
    manifest = json.loads(project.manifest.read_text())
    manifest["environment"]["hardware"]["cpu_count"] = None
    assert validate_manifest(manifest) is manifest


def test_inspect_shows_the_hardware(project):
    project.capture(record_hardware=True)
    assert "hardware:" in describe(load_manifest(project.manifest))


def _with_hardware(project, mutate=None):
    project.capture(record_hardware=True)
    data = json.loads(project.manifest.read_text())
    if mutate:
        mutate(data["environment"]["hardware"])
    project.manifest.write_text(json.dumps(data))
    return project.manifest


def test_a_rerun_on_different_hardware_is_reported(project, monkeypatch):
    path = _with_hardware(project, lambda h: h.update(cpu_count=h["cpu_count"] + 64))
    report = verify(path, rerun=True, assume_yes=True)
    assert any("CPU count:" in w for w in report.warnings)
    assert report.status is Status.PASS  # a warning, as for other environment differences
    strict = verify(path, rerun=True, assume_yes=True, strict_env=True)
    assert strict.status is Status.INCONCLUSIVE and project.runs() == 2  # nothing ran the 2nd time


def test_changed_gpus_are_reported(project, monkeypatch):
    path = _with_hardware(
        project,
        lambda h: h.update(
            gpus=[{"name": "Old GPU", "driver": "1", "memory_mib": 1}], gpu_probe="nvidia-smi"
        ),
    )
    report = verify(path, rerun=True, assume_yes=True)
    assert any("GPUs: Old GPU (driver 1) were used, now none" in w for w in report.warnings)


def test_the_same_hardware_gives_no_warning(project):
    path = _with_hardware(project)
    report = verify(path, rerun=True, assume_yes=True)
    assert not [w for w in report.warnings if "CPU count" in w or "GPUs" in w]


def test_a_manifest_without_hardware_is_not_compared(project):
    project.capture()
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert not [w for w in report.warnings if "CPU count" in w]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda h: h.update(cpu_count=0), "positive integer"),
        (lambda h: h.update(cpu_count=True), "positive integer"),
        (lambda h: h.update(processor=5), "string of at most 200"),
        (lambda h: h.update(gpu_probe="cuda"), "'nvidia-smi', 'none' or 'failed'"),
        (lambda h: h.update(gpus="x"), "list of at most"),
        (
            lambda h: h.update(gpus=[{"name": 1, "driver": "x", "memory_mib": 1}]),
            "string of at most 100",
        ),
        (
            lambda h: h.update(gpus=[{"name": "a", "driver": "x" * 41, "memory_mib": 1}]),
            "string of at most 40",
        ),
        (
            lambda h: h.update(gpus=[{"name": "a", "driver": "x", "memory_mib": -1}]),
            "non-negative integer",
        ),
        (
            lambda h: h.update(
                gpus=[{"name": "a", "driver": "x", "memory_mib": 1}] * (MAX_GPUS + 1)
            ),
            "at most",
        ),
    ],
)
def test_invalid_hardware_in_a_manifest_is_refused(project, mutate, message):
    with pytest.raises(ManifestError, match=message):
        load_manifest(_with_hardware(project, mutate))


def test_hardware_needs_schema_1_1(project):
    project.capture(record_hardware=True)
    data = json.loads(project.manifest.read_text())
    data["schema_version"] = "1.0"
    project.manifest.write_text(json.dumps(data))
    with pytest.raises(ManifestError, match="needs schema_version 1.1"):
        load_manifest(project.manifest)


def test_diff_compares_hardware(project):
    first = project.capture(name="a", record_hardware=True)
    data = json.loads(first.manifest_path.read_text())
    data["environment"]["hardware"]["cpu_count"] += 1
    data["environment"]["hardware"]["gpus"] = [{"name": "X", "driver": "1", "memory_mib": 1}]
    changed = project.root / "b.sciproof.json"
    changed.write_text(json.dumps(data))
    # b has no snapshot files of its own, so compare only the environment section
    report = diff_manifests(first.manifest_path, changed)
    env = {c.subject: c.message for c in report.changes if c.section == "environment"}
    assert "CPU count" in env and env["GPUs"].endswith("-> X (1)")
    plain = project.capture(name="c")
    one_sided = diff_manifests(first.manifest_path, plain.manifest_path)
    assert any("hardware was recorded in one manifest only" in n for n in one_sided.notes)


def test_the_cli_records_hardware(project, capsys):
    code = ["capture", "--name", "h", "--record-hardware", "--input", "data.json", "--"]
    assert cli.main([*code, sys.executable, "-c", "pass"]) == cli.EXIT_OK
    assert "hardware" in json.loads((project.root / "h.sciproof.json").read_text())["environment"]


# ---------------------------------------------------------------- OpenLineage


def test_the_run_becomes_a_start_and_a_complete_event(project):
    project.capture()
    start, end = to_openlineage(project.manifest)
    assert (start["eventType"], end["eventType"]) == ("START", "COMPLETE")
    for event in (start, end):
        assert event["producer"] == OPENLINEAGE_PRODUCER
        assert event["schemaURL"] == OPENLINEAGE_SCHEMA_URL
        assert event["job"] == {"namespace": "sciproof", "name": "exp"}
        assert {d["name"] for d in event["inputs"]} == {"data.json", "experiment.py"}
        assert {d["name"] for d in event["outputs"]} == {"out/exact.txt", "out/result.json"}
        assert all(d["namespace"] == "sciproof-files" for d in event["inputs"] + event["outputs"])
    assert start["run"] == end["run"]


def test_the_run_id_is_a_uuid_that_depends_only_on_the_manifest(project):
    project.capture(name="a")
    project.capture(name="b")
    run_a = to_openlineage(project.root / "a.sciproof.json")[0]["run"]["runId"]
    again = to_openlineage(project.root / "a.sciproof.json")[0]["run"]["runId"]
    run_b = to_openlineage(project.root / "b.sciproof.json")[0]["run"]["runId"]
    assert uuid.UUID(run_a).version == 5
    assert run_a == again and run_a != run_b


def test_the_times_are_the_capture_time_and_the_duration_before_it(project):
    project.capture()
    manifest = load_manifest(project.manifest)
    start, end = to_openlineage(project.manifest)
    started, ended = (
        datetime.fromisoformat(start["eventTime"]),
        datetime.fromisoformat(end["eventTime"]),
    )
    assert ended.isoformat() == manifest["created_at"]
    assert (ended - started).total_seconds() == pytest.approx(
        manifest["execution"]["duration_seconds"]
    )


def test_a_failed_command_ends_with_a_fail_event(project, monkeypatch):
    monkeypatch.setenv("SCIPROOF_TEST_EXIT", "3")
    project.capture()
    assert to_openlineage(project.manifest)[1]["eventType"] == "FAIL"


def test_absent_outputs_and_directories(project):
    project.capture(
        argv=[sys.executable, "-c", "import sys; sys.exit(1)"],
        inputs=["data.json"],
        outputs=["never.txt"],
        numeric_outputs=[],
    )
    assert to_openlineage(project.root / "exp.sciproof.json")[1]["outputs"] == []
    code = (
        "import pathlib; d = pathlib.Path('res'); d.mkdir(exist_ok=True); (d / 'a').write_text('a')"
    )
    project.capture(
        name="dirs",
        argv=[sys.executable, "-c", code],
        inputs=["data.json"],
        outputs=["res"],
        numeric_outputs=[],
    )
    outputs = to_openlineage(project.root / "dirs.sciproof.json")[1]["outputs"]
    assert outputs == [{"namespace": "sciproof-files", "name": "res"}]  # one dataset per directory


def test_openlineage_events_are_strict_json(project, capsys):
    project.capture()
    assert cli.main(["export", "exp.sciproof.json", "--format", "openlineage"]) == cli.EXIT_OK
    events = json.loads(
        capsys.readouterr().out, parse_constant=lambda c: pytest.fail(f"non-strict constant {c}")
    )
    assert [e["eventType"] for e in events] == ["START", "COMPLETE"]


def test_an_unknown_export_format_is_refused(project, capsys):
    project.capture()
    with pytest.raises(SystemExit):
        cli.main(["export", "exp.sciproof.json", "--format", "spdx"])
    assert "invalid choice" in capsys.readouterr().err
