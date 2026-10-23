"""``sciproof update``: capture the recorded experiment again and show what changed."""

from __future__ import annotations

import json
import sys

import pytest

from sciproof import cli
from sciproof.claims import claim_expression, parse_claim
from sciproof.errors import CaptureError, ConfirmationRequired, UsageError
from sciproof.update import declarations_of, describe_update, update_baseline
from sciproof.verify import Status, verify

CLAIMS = [
    ("out/result.json", "$.value >= 42"),
    ("out/result.json", '$.label == "ok"'),
    ("out/result.json", "$.value ~= 42 rtol=0.01 atol=0.5"),
    ("out/result.json", "$.value != null"),
]


# ---------------------------------------------------------------- claims round trip


@pytest.mark.parametrize(
    "expression",
    [
        "$.a >= 0.9",
        '$.s == "two words"',
        "$.a ~= 1 rtol=0.001 atol=1e-09",
        "$.a ~= 0.5",
        "$.a != null",
        "$.flag == true",
        "r3c1 < 10",
    ],
)
def test_a_claim_expression_round_trips(expression):
    output = "t.csv" if expression.startswith("r") else "m.json"
    claim = parse_claim(output, expression)
    assert parse_claim(output, claim_expression(claim)) == claim


def test_declarations_are_read_back_from_a_manifest(project):
    project.capture(claims=CLAIMS, note="baseline", record_hardware=True, rtol=1e-6, atol=1e-9)
    d = declarations_of(json.loads(project.manifest.read_text()))
    assert d.name == "exp" and d.argv[1:] == ["experiment.py"]
    assert d.inputs == ["data.json", "experiment.py"]
    assert d.outputs == ["out/exact.txt"] and d.numeric_outputs == ["out/result.json"]
    assert (d.rtol, d.atol, d.note, d.record_hardware) == (1e-6, 1e-9, "baseline", True)
    assert [parse_claim(o, e) for o, e in d.claims] == [parse_claim(o, e) for o, e in CLAIMS]


def test_different_tolerances_per_output_cannot_be_updated(project):
    project.capture()
    data = json.loads(project.manifest.read_text())
    second = dict(data["outputs"][1])
    second.update(path="out/other.json", compare={**second["compare"], "rtol": 0.5})
    data["outputs"].append(second)
    with pytest.raises(UsageError, match="different tolerances"):
        declarations_of(data)


# ---------------------------------------------------------------- the update


def test_an_update_replaces_the_manifest_and_shows_what_changed(project):
    project.capture(claims=CLAIMS, note="baseline")
    (project.root / "data.json").write_text('{"x": 30}')
    runs = project.runs()
    result = update_baseline(project.manifest, assume_yes=True)
    assert project.runs() == runs + 1
    manifest = json.loads(project.manifest.read_text())
    assert manifest["note"] == "baseline" and len(manifest["claims"]) == len(CLAIMS)
    assert result.capture.exit_code == 0
    # the claim '~= 42' no longer holds for 60: it is recorded anyway, with a warning
    assert any("does not hold at capture time" in w and "~= 42" in w for w in result.warnings)
    sections = {c.section: c for c in result.diff.changes}
    assert "inputs" in sections and "outputs" in sections
    outputs = [
        c for c in result.diff.changes if c.section == "outputs" and c.subject == "out/result.json"
    ]
    assert "recorded 42.0, found 60.0" in outputs[0].message
    # the new evidence is what is on disk now: its files match it, and the failing claim shows
    report = verify(project.manifest)
    assert all(c.status is Status.PASS for c in report.checks if c.subject.startswith("output "))
    assert [c.subject for c in report.checks if c.status is Status.FAIL] == [
        "claim out/result.json: $.value ~= 42 (rtol=0.01, atol=0.5)"
    ]


def test_an_update_without_changes_reports_no_difference(project):
    project.capture()
    result = update_baseline(project.manifest, assume_yes=True)
    assert result.diff.identical


def test_the_new_manifest_keeps_numeric_tolerance_and_hardware(project):
    project.capture(rtol=1e-6, atol=1e-9, record_hardware=True)
    update_baseline(project.manifest, assume_yes=True)
    data = json.loads(project.manifest.read_text())
    compare = next(o["compare"] for o in data["outputs"] if o["path"] == "out/result.json")
    assert (compare["rtol"], compare["atol"]) == (1e-6, 1e-9)
    assert "hardware" in data["environment"]


@pytest.mark.parametrize("limit", [0, 2])
def test_the_log_limit_survives_an_update(project, limit):
    # --max-log-bytes 0 keeps a command's output out of the manifest; an update must not put it back
    project.capture(max_log_bytes=limit)
    before = json.loads(project.manifest.read_text())["execution"]["stdout"]
    assert before["truncated"] and before["text"] == "done"[:limit]
    update_baseline(project.manifest, assume_yes=True)
    after = json.loads(project.manifest.read_text())["execution"]["stdout"]
    assert after["truncated"] and after["text"] == before["text"]


def test_an_update_keeps_the_default_when_no_log_was_cut(project):
    project.capture()
    data = json.loads(project.manifest.read_text())
    assert declarations_of(data).max_log_bytes == 64 * 1024


def test_a_long_uncut_log_does_not_lower_the_limit(project):
    project.capture()
    data = json.loads(project.manifest.read_text())
    data["execution"]["stdout"]["size"] = 1_000_000  # recorded with a limit above the default
    assert declarations_of(data).max_log_bytes == 1_000_000


def test_the_note_can_be_replaced(project):
    project.capture(note="old")
    update_baseline(project.manifest, assume_yes=True, note="new")
    assert json.loads(project.manifest.read_text())["note"] == "new"


def test_directories_are_updated_too(project):
    code = (
        "import pathlib, os; d = pathlib.Path('res'); d.mkdir(exist_ok=True); "
        "(d / 'a.txt').write_text(os.environ.get('SCIPROOF_TEST_SUFFIX', 'v1'))"
    )
    project.capture(
        argv=[sys.executable, "-c", code], inputs=["data.json"], outputs=["res"], numeric_outputs=[]
    )
    # nothing else changed, but the environment now makes the command write something else
    import os

    os.environ["SCIPROOF_TEST_SUFFIX"] = "v2"
    try:
        result = update_baseline(project.root / "exp.sciproof.json", assume_yes=True)
    finally:
        del os.environ["SCIPROOF_TEST_SUFFIX"]
    change = next(c for c in result.diff.changes if c.subject == "res")
    assert "1 modified (a.txt)" in change.message


# ---------------------------------------------------------------- safety


def test_nothing_runs_without_confirmation(project):
    project.capture()
    runs = project.runs()
    before = project.manifest.read_bytes()
    with pytest.raises(ConfirmationRequired):
        update_baseline(project.manifest)
    assert update_baseline(project.manifest, confirm=lambda prompt: False) is None
    assert project.runs() == runs and project.manifest.read_bytes() == before


def test_the_prompt_shows_the_command_and_what_is_replaced(project):
    project.capture()
    prompts: list[str] = []

    def decline(prompt):
        prompts.append(prompt)
        return False

    update_baseline(project.manifest, confirm=decline)
    text = prompts[0]
    assert "EXECUTE the command" in text and "experiment.py" in text
    assert "exp.sciproof.json and exp.sciproof.d/" in text and "out/result.json" in text
    assert (
        describe_update(project.manifest, declarations_of(json.loads(project.manifest.read_text())))
        == text
    )


def test_a_command_that_writes_nothing_cannot_turn_old_outputs_into_the_baseline(project):
    project.capture()
    data = json.loads(project.manifest.read_text())
    data["command"]["argv"] = [sys.executable, "-c", "pass"]
    project.manifest.write_text(json.dumps(data))
    before = {p: (project.root / p).read_bytes() for p in ("out/exact.txt", "out/result.json")}
    manifest_before = project.manifest.read_bytes()
    with pytest.raises(CaptureError, match="does not exist"):
        update_baseline(project.manifest, assume_yes=True)
    assert {p: (project.root / p).read_bytes() for p in before} == before  # restored
    assert project.manifest.read_bytes() == manifest_before  # and the old evidence is untouched


def test_a_failing_command_is_recorded_and_reported(project, monkeypatch):
    project.capture()
    monkeypatch.setenv("SCIPROOF_TEST_EXIT", "3")
    result = update_baseline(project.manifest, assume_yes=True)
    assert result.capture.exit_code == 3
    assert any(c.subject == "exit code" for c in result.diff.changes)


def test_a_renamed_manifest_is_refused(project):
    project.capture()
    renamed = project.root / "other.sciproof.json"
    renamed.write_bytes(project.manifest.read_bytes())
    with pytest.raises(UsageError, match="not named after its experiment"):
        update_baseline(renamed, assume_yes=True)


def test_an_old_signature_is_flagged(project):
    project.capture()
    (project.root / "exp.sciproof.json.sig").write_text("signature of the old manifest")
    result = update_baseline(project.manifest, assume_yes=True)
    assert any("signs the OLD manifest" in w for w in result.warnings)


# ---------------------------------------------------------------- command line


def test_cli_update(project, capsys):
    project.capture()
    (project.root / "data.json").write_text('{"x": 7}')
    assert cli.main(["update", "exp.sciproof.json", "--yes"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("Updated exp.sciproof.json (command exit code 0)")
    assert "SciProof diff: 'exp' -> 'exp'" in out and "RESULT: DIFFERENT" in out


def test_cli_dry_run_executes_nothing(project, capsys):
    project.capture()
    runs = project.runs()
    assert cli.main(["update", "exp.sciproof.json", "--dry-run"]) == cli.EXIT_OK
    assert "EXECUTE the command" in capsys.readouterr().out
    assert project.runs() == runs


def test_cli_without_a_terminal_and_without_yes_refuses(project, capsys, monkeypatch):
    project.capture()
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli.main(["update", "exp.sciproof.json"]) == cli.EXIT_ERROR
    assert "no terminal to ask for confirmation" in capsys.readouterr().err


def test_cli_a_failing_command_exits_with_one(project, monkeypatch, capsys):
    project.capture()
    monkeypatch.setenv("SCIPROOF_TEST_EXIT", "2")
    assert cli.main(["update", "exp.sciproof.json", "--yes"]) == cli.EXIT_FAIL
    assert "The command failed" in capsys.readouterr().err
