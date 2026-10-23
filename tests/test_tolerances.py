"""``--tolerance``: one numeric output gets its own rtol/atol instead of the default."""

from __future__ import annotations

import json
import sys

import pytest

from sciproof import cli
from sciproof.errors import CaptureError, UsageError
from sciproof.verify import Status, verify

WRITE = (
    "import json, os, sys; os.makedirs('out', exist_ok=True); "
    "[json.dump({'v': float(os.environ.get('V_' + n, '1.0'))}, open(f'out/{n}.json', 'w')) "
    "for n in 'ab']"
)


def _capture(project, **overrides):
    options = {
        "argv": [sys.executable, "-c", WRITE],
        "outputs": [],
        "numeric_outputs": ["out/a.json", "out/b.json"],
        "rtol": 1e-9,
        "atol": 0.0,
    }
    options.update(overrides)
    return project.capture(**options)


def _compare(project):
    data = json.loads(project.manifest.read_text())
    return {o["path"]: (o["compare"]["rtol"], o["compare"]["atol"]) for o in data["outputs"]}


def test_a_tolerance_applies_to_its_output_only(project):
    _capture(project, tolerances={"out/b.json": {"rtol": 0.1}})
    assert _compare(project) == {"out/a.json": (1e-9, 0.0), "out/b.json": (0.1, 0.0)}


def test_a_setting_that_is_left_out_keeps_the_default(project):
    _capture(project, rtol=1e-4, atol=1e-8, tolerances={"out/a.json": {"atol": 0.5}})
    assert _compare(project)["out/a.json"] == (1e-4, 0.5)
    assert _compare(project)["out/b.json"] == (1e-4, 1e-8)


def test_a_rerun_uses_the_tolerance_of_each_output(project, monkeypatch):
    _capture(project, tolerances={"out/b.json": {"rtol": 0.1}})
    monkeypatch.setenv("V_a", "1.05")  # 5 % off, outside the default of a.json
    monkeypatch.setenv("V_b", "1.05")  # 5 % off, inside the 10 % of b.json
    report = verify(project.manifest, rerun=True, assume_yes=True)
    states = {c.subject: c.status for c in report.checks}
    assert states["rerun output out/a.json"] is Status.FAIL
    assert states["rerun output out/b.json"] is Status.PASS


def test_a_tolerance_for_an_undeclared_or_exact_output_is_refused(project):
    with pytest.raises(CaptureError, match="--output-numeric"):
        _capture(project, tolerances={"out/zzz.json": {"rtol": 0.1}})
    with pytest.raises(CaptureError, match="--output-numeric"):
        _capture(
            project,
            outputs=["out/exact.txt"],
            argv=[sys.executable, "experiment.py"],
            numeric_outputs=["out/result.json"],
            tolerances={"out/exact.txt": {"rtol": 0.1}},
        )
    assert not project.manifest.exists()


@pytest.mark.parametrize(
    "settings",
    [{"rtol": -1.0}, {"atol": float("nan")}, {"rtol": float("inf")}, {"mtol": 1.0}],
)
def test_bad_tolerance_settings_are_refused(project, settings):
    with pytest.raises(CaptureError):
        _capture(project, tolerances={"out/a.json": settings})
    assert not project.manifest.exists()


def test_a_large_rtol_for_one_output_warns_about_that_output(project):
    result = _capture(project, tolerances={"out/b.json": {"rtol": 2.0}})
    assert any("out/b.json" in w and "rtol=2.0" in w for w in result.warnings)
    assert not any("out/a.json" in w for w in result.warnings)


def test_the_cli_option_is_parsed(project, capsys):
    code = cli.main(
        [
            "capture", "--name", "exp", "--output-numeric", "out/a.json",
            "--output-numeric", "out/b.json", "--rtol", "1e-6",
            "--tolerance", "out/b.json", "rtol=1e-2, atol=1e-5",
            "--", sys.executable, "-c", WRITE,
        ]
    )  # fmt: skip
    assert code == cli.EXIT_OK, capsys.readouterr().err
    assert _compare(project) == {"out/a.json": (1e-6, 0.0), "out/b.json": (1e-2, 1e-5)}


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ("", "no setting given"),
        ("rtol", "expected 'rtol=X'"),
        ("rtol=abc", "must be a number"),
        ("rtol=1 rtol=2", "given twice"),
        ("ctol=1", "expected 'rtol=X'"),
    ],
)
def test_the_cli_option_rejects_malformed_settings(project, capsys, settings, message):
    code = cli.main(
        [
            "capture", "--name", "exp", "--output-numeric", "out/a.json",
            "--tolerance", "out/a.json", settings, "--", sys.executable, "-c", WRITE,
        ]
    )  # fmt: skip
    assert code == cli.EXIT_ERROR
    assert message in capsys.readouterr().err


def test_the_same_output_twice_is_refused():
    with pytest.raises(UsageError, match="given twice"):
        cli._parse_tolerances([["a.json", "rtol=1"], ["a.json", "atol=1"]])
