from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from sciproof.capture import capture
from sciproof.environment import ENV_ALLOWLIST
from sciproof.errors import CaptureError
from sciproof.manifest import load_manifest
from sciproof.runner import CommandInterrupted, run_command


def test_capture_writes_a_valid_manifest(project):
    result = project.capture()
    assert result.exit_code == 0
    assert project.manifest.is_file()

    manifest = load_manifest(project.manifest)  # validates the structure
    assert manifest["name"] == "exp"
    assert manifest["command"]["argv"][1] == "experiment.py"
    assert {item["path"] for item in manifest["inputs"]} == {"data.json", "experiment.py"}
    by_path = {item["path"]: item for item in manifest["outputs"]}
    assert by_path["out/exact.txt"]["compare"] == {"mode": "exact"}
    assert by_path["out/result.json"]["compare"]["mode"] == "numeric"
    assert manifest["execution"]["stdout"]["text"].strip() == "done"
    assert manifest["environment"]["python"]["version"].count(".") == 2
    assert (project.root / by_path["out/result.json"]["compare"]["snapshot"]["path"]).is_file()
    assert project.runs() == 1


def test_manifest_uses_relative_paths_and_no_absolute_directories(project):
    project.capture()
    text = project.manifest.read_text()
    assert str(project.root) not in text
    assert str(project.root).replace("\\", "/") not in text


def test_secrets_in_the_environment_are_not_recorded(project, monkeypatch):
    for name in ENV_ALLOWLIST:  # the runner may define some of them (for example LANG)
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MY_SECRET_TOKEN", "hunter2-super-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "abcd-secret-key")
    monkeypatch.setenv("TZ", "UTC")
    project.capture()
    text = project.manifest.read_text()
    assert "hunter2-super-secret" not in text
    assert "abcd-secret-key" not in text
    assert json.loads(text)["environment"]["env_vars"] == {"TZ": "UTC"}


def test_nonzero_exit_code_is_recorded(project, monkeypatch):
    monkeypatch.setenv("SCIPROOF_TEST_EXIT", "3")
    result = project.capture()
    assert result.exit_code == 3
    assert load_manifest(project.manifest)["execution"]["exit_code"] == 3


def test_failed_command_that_produced_no_output_records_it_as_absent(project):
    result = project.capture(
        argv=[sys.executable, "-c", "import sys; sys.exit(5)"],
        outputs=["out/never.txt"],
        numeric_outputs=[],
    )
    assert result.exit_code == 5
    assert load_manifest(project.manifest)["outputs"][0]["present"] is False


def test_successful_command_must_produce_declared_outputs(project):
    with pytest.raises(CaptureError, match="does not exist"):
        project.capture(argv=[sys.executable, "-c", "pass"], outputs=["out/never.txt"])
    assert not project.manifest.exists()


def test_command_not_found_is_a_clean_error(project):
    with pytest.raises(CaptureError, match="cannot start"):
        project.capture(argv=["this-command-does-not-exist-sciproof"])
    assert not project.manifest.exists()


@pytest.mark.parametrize("argv", [[], [""]])
def test_empty_command_is_rejected(project, argv):
    with pytest.raises(CaptureError, match="no command"):
        project.capture(argv=argv)


def test_interrupted_capture_writes_no_evidence(project, monkeypatch):
    def interrupted(*args, **kwargs):
        raise CommandInterrupted("interrupted by the user")

    monkeypatch.setattr("sciproof.capture.run_command", interrupted)
    with pytest.raises(CaptureError, match="interrupted"):
        project.capture()
    assert not project.manifest.exists()


def test_ctrl_c_during_the_command_becomes_command_interrupted(tmp_path, monkeypatch):
    real_communicate = subprocess.Popen.communicate
    calls = []

    def interrupted(self, *args, **kwargs):
        if not calls:  # the first call is the one waiting for the command
            calls.append(1)
            raise KeyboardInterrupt
        return real_communicate(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    started = time.monotonic()
    with pytest.raises(CommandInterrupted):
        run_command([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, None)
    assert time.monotonic() - started < 15  # the command was stopped, not waited for


def test_timeout_writes_no_evidence(project):
    with pytest.raises(CaptureError, match="did not finish"):
        project.capture(
            argv=[sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.5,
        )
    assert not project.manifest.exists()


def test_missing_input_is_an_error(project):
    with pytest.raises(CaptureError, match="input file not found"):
        project.capture(inputs=["data.json", "does-not-exist.csv"])


def test_a_directory_as_input_is_recorded_as_a_tree(project):
    """Directories became supported in schema 1.1 (see tests/test_directories.py)."""
    (project.root / "folder").mkdir()
    (project.root / "folder" / "a.txt").write_text("a")
    result = project.capture(inputs=["data.json", "folder"])
    entry = next(item for item in result.manifest["inputs"] if item["path"] == "folder")
    assert entry["kind"] == "directory"
    assert [f["path"] for f in entry["files"]] == ["a.txt"]
    assert result.manifest["schema_version"] == "1.1"


def test_a_directory_as_output_is_reported_as_such(project):
    (project.root / "folder.json").mkdir()
    with pytest.raises(CaptureError, match="is a directory"):
        project.capture(outputs=[], numeric_outputs=["folder.json"])
    assert project.runs() == 0


def test_command_must_not_modify_declared_inputs(project):
    with pytest.raises(CaptureError, match="modified or removed the declared input"):
        project.capture(
            argv=[sys.executable, "-c", "open('data.json', 'w').write('{}')"],
            outputs=[],
            numeric_outputs=[],
        )
    assert not project.manifest.exists()


def test_refuses_to_overwrite_without_force(project):
    project.capture()
    with pytest.raises(CaptureError, match="already exists"):
        project.capture()
    project.capture(force=True)


@pytest.mark.parametrize("name", ["", "../escape", "a/b", "with space", "-leading", "x" * 101])
def test_invalid_names_are_rejected(project, name):
    with pytest.raises(CaptureError, match="invalid name"):
        project.capture(name=name)


def test_at_least_one_file_must_be_declared(project):
    with pytest.raises(CaptureError, match="declare at least one"):
        project.capture(inputs=[], outputs=[], numeric_outputs=[])


def test_input_and_output_must_not_overlap(project):
    with pytest.raises(CaptureError, match="both input and output"):
        project.capture(outputs=["data.json"])


def test_numeric_outputs_must_be_json_or_csv(project):
    with pytest.raises(CaptureError, match="supports"):
        project.capture(numeric_outputs=["out/exact.txt"], outputs=[])


def test_log_output_is_truncated_but_fully_hashed(project):
    result = project.capture(
        argv=[sys.executable, "-c", "print('x' * 5000)"],
        outputs=[],
        numeric_outputs=[],
        max_log_bytes=100,
    )
    stdout = result.manifest["execution"]["stdout"]
    assert stdout["truncated"] is True
    assert len(stdout["text"]) == 100
    assert stdout["size"] == 5001 + (1 if sys.platform == "win32" else 0)


@pytest.mark.parametrize(
    "options, message",
    [
        ({"rtol": -1}, "not negative"),
        ({"atol": -1e-9}, "not negative"),
        ({"rtol": float("nan")}, "finite"),
        ({"atol": float("inf")}, "finite"),
        ({"timeout": 0}, "timeout"),
        ({"timeout": -3}, "timeout"),
        ({"timeout": float("nan")}, "timeout"),
        ({"max_log_bytes": -5}, "max-log-bytes"),
    ],
)
def test_invalid_options_are_rejected_before_the_command_runs(project, options, message):
    with pytest.raises(CaptureError, match=message):
        project.capture(**options)
    assert project.runs() == 0
    assert not project.manifest.exists()


def test_capture_function_is_usable_without_the_cli(project):
    result = capture(
        name="direct",
        argv=[sys.executable, "-c", "print(1)"],
        base=project.root,
        inputs=["data.json"],
    )
    assert result.manifest_path.name == "direct.sciproof.json"


GRANDCHILD = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', "
    '\'import time, pathlib; time.sleep(4); pathlib.Path("late.txt").write_text("x")\']); '
    "time.sleep(60)"
)


def test_timeout_is_enforced_when_the_command_leaves_a_child_running(project):
    started = time.monotonic()
    with pytest.raises(CaptureError, match="did not finish"):
        project.capture(argv=[sys.executable, "-c", GRANDCHILD], timeout=1)
    assert time.monotonic() - started < 3.5  # not the 4+ seconds the child needs to exit
    assert not project.manifest.exists()

    time.sleep(4.5)  # long enough for a surviving child to have written its marker
    assert not (project.root / "late.txt").exists(), "the child process survived the timeout"


def _snapshot_files(project):
    folder = project.root / "exp.sciproof.d"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


TWO_NUMERIC = (
    "import pathlib; "
    "pathlib.Path('p.json').write_text('[1]'); "
    "pathlib.Path('q.json').write_text('[2]')"
)


def test_force_replaces_the_snapshots_and_drops_stale_ones(project):
    argv = [sys.executable, "-c", TWO_NUMERIC]
    project.capture(argv=argv, outputs=[], numeric_outputs=["p.json", "q.json"])
    assert _snapshot_files(project) == ["0-p.json", "1-q.json"]

    project.capture(argv=argv, outputs=[], numeric_outputs=["p.json"], force=True)
    assert _snapshot_files(project) == ["0-p.json"]  # 1-q.json is no longer referenced


def test_force_without_numeric_outputs_removes_the_snapshot_directory(project):
    argv = [sys.executable, "-c", TWO_NUMERIC]
    project.capture(argv=argv, outputs=[], numeric_outputs=["p.json"])
    project.capture(argv=argv, outputs=["q.json"], numeric_outputs=[], force=True)
    assert not (project.root / "exp.sciproof.d").exists()


def test_a_failed_capture_leaves_the_earlier_evidence_intact(project):
    argv = [sys.executable, "-c", TWO_NUMERIC]
    project.capture(argv=argv, outputs=[], numeric_outputs=["p.json"])
    before = (project.root / "exp.sciproof.d" / "0-p.json").read_text()
    manifest_before = project.manifest.read_text()

    with pytest.raises(CaptureError):  # p.json is fine, but missing.json is never written
        project.capture(
            argv=argv, outputs=[], numeric_outputs=["p.json", "missing.json"], force=True
        )

    assert (project.root / "exp.sciproof.d" / "0-p.json").read_text() == before
    assert project.manifest.read_text() == manifest_before
    assert not (project.root / "exp.sciproof.d.tmp").exists()
    assert _snapshot_files(project) == ["0-p.json"]


def test_claim_on_missing_output_file(project):
    with pytest.raises(CaptureError, match="the output file does not exist"):
        project.capture(
            name="test-run",
            # Create a directory named 'out/custom.json'
            argv=[sys.executable, "-c", "import os; os.makedirs('out/custom.json', exist_ok=True)"],
            outputs=["out/custom.json"],
            numeric_outputs=[],  # Clear default numeric outputs
            claims=[("out/custom.json", "$.value >= 1")],
        )

    assert not project.manifest.exists()


def test_duplicate_input_paths_are_rejected(project):
    with pytest.raises(CaptureError, match="contains the same path more than once"):
        project.capture(
            name="test-dup",
            argv=[sys.executable, "-c", "pass"],
            inputs=["data.json", "data.json"],  # Give the same input twice
            outputs=[],
            numeric_outputs=[],
        )
