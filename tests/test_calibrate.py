"""``sciproof calibrate``: how much do the outputs of the recorded command vary between runs?"""

from __future__ import annotations

import json
import sys

import pytest

from sciproof import cli
from sciproof.calibrate import calibrate
from sciproof.errors import ConfirmationRequired, UsageError

# Each run adds 1e-7 to the number (a counter file in the working directory makes the "noise"
# reproducible); `exact` is different in every run only when asked.
NOISY = r"""\
import json, os, pathlib, sys
n = len(pathlib.Path("count.txt").read_text().splitlines()) if os.path.exists("count.txt") else 0
with open("count.txt", "a") as handle:
    handle.write("x\n")
step = float(os.environ.get("STEP", "1e-7"))
os.makedirs("out", exist_ok=True)
json.dump({"loss": 1.0 + n * step, "label": "ok", "n": 3}, open("out/m.json", "w"))
pathlib.Path("out/e.txt").write_text(str(n) if os.environ.get("VARY") else "same")
"""


@pytest.fixture
def noisy(project, monkeypatch):
    (project.root / "noisy.py").write_text(NOISY)
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    def make(**overrides):
        options = {
            "argv": [sys.executable, "noisy.py"],
            "inputs": ["noisy.py"],
            "outputs": ["out/e.txt"],
            "numeric_outputs": ["out/m.json"],
            "rtol": 1e-3,
        }
        options.update(overrides)
        (project.root / "count.txt").unlink(missing_ok=True)
        project.capture(**options)
        (project.root / "count.txt").unlink(missing_ok=True)
        return project

    return make


def test_noise_inside_the_tolerance_is_stable(noisy):
    project = noisy()
    report = calibrate(project.manifest, runs=3, assume_yes=True)
    numeric = next(o for o in report.outputs if o.path == "out/m.json")
    assert report.ok and numeric.ok and numeric.suggestion is None
    assert 0 < numeric.max_rel < 1e-3
    assert "inside the recorded tolerance" in numeric.message
    assert report.to_text().endswith("RESULT: STABLE")


def test_noise_outside_the_tolerance_suggests_one_that_covers_it(noisy):
    project = noisy(rtol=1e-12)
    report = calibrate(project.manifest, runs=4, assume_yes=True)
    numeric = next(o for o in report.outputs if o.path == "out/m.json")
    assert not report.ok and not numeric.ok
    assert "outside the recorded tolerance" in numeric.message
    assert numeric.suggestion.startswith("--tolerance out/m.json 'rtol=")
    suggested = float(numeric.suggestion.split("rtol=")[1].rstrip("'"))
    assert suggested >= numeric.max_rel * 10  # ten times the spread, rounded up
    assert report.to_text().endswith("RESULT: VARIES")


def test_the_suggested_tolerance_makes_the_same_runs_stable(noisy):
    first = noisy(rtol=1e-12)
    numeric = calibrate(first.manifest, runs=4, assume_yes=True).outputs[1]
    assert numeric.suggestion
    rtol = float(numeric.suggestion.split("rtol=")[1].rstrip("'"))
    second = noisy(rtol=rtol, force=True)  # captured again with the suggestion
    assert calibrate(second.manifest, runs=4, assume_yes=True).ok


def test_an_exact_output_that_differs_between_runs_is_reported(noisy, monkeypatch):
    project = noisy()
    monkeypatch.setenv("VARY", "1")
    report = calibrate(project.manifest, runs=3, assume_yes=True)
    exact = next(o for o in report.outputs if o.path == "out/e.txt")
    assert not exact.ok and "not deterministic" in exact.message
    assert "--output-numeric" in exact.message


def test_identical_runs_are_reported_as_identical(noisy, monkeypatch):
    project = noisy()
    monkeypatch.setenv("STEP", "0")
    (project.root / "count.txt").unlink(missing_ok=True)
    report = calibrate(project.manifest, runs=2, assume_yes=True)
    assert all("identical" in o.message for o in report.outputs)


def test_the_working_tree_and_the_manifest_are_left_as_they_were(noisy):
    project = noisy()
    manifest_before = project.manifest.read_bytes()
    out_before = (project.root / "out" / "m.json").read_bytes()
    exact_before = (project.root / "out" / "e.txt").read_bytes()
    calibrate(project.manifest, runs=3, assume_yes=True)
    assert project.manifest.read_bytes() == manifest_before
    assert (project.root / "out" / "m.json").read_bytes() == out_before
    assert (project.root / "out" / "e.txt").read_bytes() == exact_before


def test_outputs_that_did_not_exist_before_are_removed_again(noisy):
    project = noisy()
    (project.root / "out" / "m.json").unlink()
    calibrate(project.manifest, runs=2, assume_yes=True)
    assert not (project.root / "out" / "m.json").exists()


def test_a_changed_exit_code_is_reported(noisy):
    project = noisy()
    data = json.loads(project.manifest.read_text())
    data["execution"]["exit_code"] = 5  # recorded 5, the runs exit with 0
    project.manifest.write_text(json.dumps(data))
    report = calibrate(project.manifest, runs=2, assume_yes=True)
    assert not report.ok and "exit code" in report.to_text()


def test_nothing_runs_without_confirmation(noisy):
    project = noisy()
    with pytest.raises(ConfirmationRequired):
        calibrate(project.manifest, runs=2)
    assert not (project.root / "count.txt").exists()
    assert calibrate(project.manifest, runs=2, confirm=lambda prompt: False) is None
    assert not (project.root / "count.txt").exists()


@pytest.mark.parametrize("runs", [0, 1, 51, -3])
def test_the_number_of_runs_is_bounded(noisy, runs):
    with pytest.raises(UsageError, match="between 2 and 50"):
        calibrate(noisy().manifest, runs=runs, assume_yes=True)


def test_cli_calibrate(noisy, capsys):
    project = noisy(rtol=1e-12)
    code = cli.main(["calibrate", project.manifest.name, "--runs", "3", "--yes"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_FAIL
    assert "suggestion: --tolerance out/m.json" in out and "RESULT: VARIES" in out


def test_cli_calibrate_without_a_terminal_refuses(noisy, capsys, monkeypatch):
    project = noisy()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli.main(["calibrate", project.manifest.name]) == cli.EXIT_ERROR
    assert "refusing to execute" in capsys.readouterr().err
