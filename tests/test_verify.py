from __future__ import annotations

import json
import shutil
import sys

import pytest

from sciproof import verify as verify_module
from sciproof.errors import ConfirmationRequired, UsageError
from sciproof.runner import MAX_WAIT_SECONDS
from sciproof.verify import Status, format_report, verify


def _status(report, subject_part):
    matching = [c for c in report.checks if subject_part in c.subject]
    assert matching, f"no check about {subject_part!r}: {[c.subject for c in report.checks]}"
    return matching[0].status


def _edit_manifest(project, mutate):
    data = json.loads(project.manifest.read_text())
    mutate(data)
    project.manifest.write_text(json.dumps(data))


# ---------------------------------------------------------------- integrity (nothing is run)


def test_unchanged_artifacts_pass_without_running_anything(project):
    project.capture()
    runs_after_capture = project.runs()
    report = verify(project.manifest)
    assert report.status is Status.PASS
    assert project.runs() == runs_after_capture
    assert any("NOT executed" in note for note in report.info)


def test_modified_output_fails(project):
    project.capture()
    (project.root / "out" / "exact.txt").write_text("tampered")
    report = verify(project.manifest)
    assert report.status is Status.FAIL
    assert _status(report, "output out/exact.txt") is Status.FAIL


def test_missing_output_fails(project):
    project.capture()
    (project.root / "out" / "exact.txt").unlink()
    report = verify(project.manifest)
    assert report.status is Status.FAIL
    assert "missing" in next(c for c in report.checks if "exact.txt" in c.subject).message


def test_modified_input_fails(project):
    project.capture()
    (project.root / "data.json").write_text('{"x": 22}')
    assert verify(project.manifest).status is Status.FAIL


def test_unavailable_input_is_inconclusive_not_a_pass(project):
    project.capture()
    (project.root / "data.json").unlink()
    report = verify(project.manifest)
    assert report.status is Status.INCONCLUSIVE
    assert _status(report, "input data.json") is Status.INCONCLUSIVE


def test_fail_takes_precedence_over_inconclusive(project):
    project.capture()
    (project.root / "data.json").unlink()
    (project.root / "out" / "exact.txt").write_text("tampered")
    assert verify(project.manifest).status is Status.FAIL


def test_output_recorded_as_absent_must_stay_absent(project):
    project.capture(
        argv=[sys.executable, "-c", "import sys; sys.exit(1)"],
        outputs=["out/never.txt"],
        numeric_outputs=[],
    )
    assert verify(project.manifest).status is Status.PASS
    (project.root / "out").mkdir(exist_ok=True)
    (project.root / "out" / "never.txt").write_text("appeared")
    assert verify(project.manifest).status is Status.FAIL


def test_tampered_numeric_snapshot_is_detected(project):
    project.capture()
    snapshot = next((project.root / "exp.sciproof.d").iterdir())
    snapshot.write_text('{"value": 0, "label": "ok"}')
    report = verify(project.manifest)
    assert report.status is Status.FAIL
    assert "snapshot" in " ".join(c.subject for c in report.checks if c.status is Status.FAIL)


def test_missing_numeric_snapshot_is_inconclusive(project):
    project.capture()
    next((project.root / "exp.sciproof.d").iterdir()).unlink()
    assert verify(project.manifest).status is Status.INCONCLUSIVE


def test_report_without_checks_is_never_a_pass(project):
    project.capture()
    report = verify(project.manifest)
    report.checks.clear()
    assert report.status is Status.INCONCLUSIVE


# ---------------------------------------------------------------------------- rerun


def test_rerun_requires_confirmation_and_runs_nothing_without_it(project):
    project.capture()
    with pytest.raises(ConfirmationRequired):
        verify(project.manifest, rerun=True)
    assert project.runs() == 1


def test_rerun_declined_by_the_user_is_inconclusive_and_runs_nothing(project):
    project.capture()
    prompts = []
    report = verify(
        project.manifest, rerun=True, confirm=lambda prompt: prompts.append(prompt) or False
    )
    assert report.status is Status.INCONCLUSIVE
    assert project.runs() == 1
    assert "experiment.py" in prompts[0]  # the user sees the command before deciding
    assert "permissions" in prompts[0]


def test_rerun_of_a_deterministic_experiment_passes(project):
    project.capture()
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.PASS, report.checks
    assert project.runs() == 2
    assert _status(report, "rerun output out/exact.txt") is Status.PASS
    assert not [w for w in report.warnings if "environment differs" in w]


@pytest.mark.parametrize("created", [False, True])
def test_rerun_checks_an_output_recorded_as_absent(project, created):
    project.capture(
        argv=[sys.executable, "-c", "raise SystemExit(1)"],
        outputs=["never.txt"],
        numeric_outputs=[],
    )
    _edit_manifest(
        project,
        lambda m: m["command"].update(
            argv=[
                sys.executable,
                "-c",
                "from pathlib import Path; "
                + ("Path('never.txt').write_text('created'); " if created else "")
                + "raise SystemExit(1)",
            ]
        ),
    )

    report = verify(project.manifest, rerun=True, assume_yes=True)
    (check,) = [c for c in report.checks if c.subject == "rerun output never.txt"]
    assert check.status is (Status.FAIL if created else Status.PASS)
    assert ("rerun created it" if created else "absent, as recorded") in check.message
    assert report.status is (Status.FAIL if created else Status.PASS)
    assert not (project.root / "never.txt").exists()


@pytest.mark.parametrize("tampered", [False, True])
def test_rerun_cannot_compare_an_unusable_numeric_snapshot(project, tampered):
    project.capture()
    data = json.loads(project.manifest.read_text())
    item = next(o for o in data["outputs"] if o["path"] == "out/result.json")
    snapshot = project.root / item["compare"]["snapshot"]["path"]
    if tampered:
        snapshot.write_text('{"value": 0, "label": "ok"}')
    else:
        snapshot.unlink()

    report = verify(project.manifest, rerun=True, assume_yes=True)
    (check,) = [c for c in report.checks if c.subject == "rerun output out/result.json"]
    assert check.status is Status.INCONCLUSIVE
    assert "recorded copy is unusable" in check.message
    assert "cannot compare numerically" in check.message
    assert report.status is (Status.FAIL if tampered else Status.INCONCLUSIVE)
    assert project.runs() == 2


def test_rerun_with_unparsable_numeric_output_fails_without_crashing(project):
    project.capture()
    original = (project.root / "out/result.json").read_bytes()
    code = "from pathlib import Path; Path('out/result.json').write_text('{')"
    _edit_manifest(project, lambda m: m["command"].update(argv=[sys.executable, "-c", code]))

    report = verify(project.manifest, rerun=True, assume_yes=True)
    (check,) = [c for c in report.checks if c.subject == "rerun output out/result.json"]
    assert check.status is Status.FAIL
    assert "cannot be compared numerically" in check.message
    assert report.status is Status.FAIL
    assert (project.root / "out/result.json").read_bytes() == original


def test_rerun_leaves_the_recorded_outputs_untouched(project, monkeypatch):
    project.capture()
    original = (project.root / "out" / "exact.txt").read_bytes()
    monkeypatch.setenv("SCIPROOF_TEST_SUFFIX", "changed")
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.FAIL
    assert (project.root / "out" / "exact.txt").read_bytes() == original
    assert report.rerun_outputs_dir is not None
    rerun_copy = project.root / report.rerun_outputs_dir / "out" / "exact.txt"
    assert rerun_copy.read_text() == "exact-changed"
    assert verify(project.manifest).status is Status.PASS


def test_rerun_removes_directories_it_created(project):
    project.capture()
    shutil.rmtree(project.root / "out")  # the outputs are gone, the rerun has to recreate them
    verify(project.manifest, rerun=True, assume_yes=True)
    assert not (project.root / "out").exists()  # no empty leftover directory


def test_rerun_keeps_directories_that_existed_before(project):
    project.capture()
    (project.root / "out" / "extra.txt").write_text("unrelated")
    verify(project.manifest, rerun=True, assume_yes=True)
    assert (project.root / "out" / "extra.txt").read_text() == "unrelated"


def test_two_reruns_in_the_same_second_use_different_directories(project, monkeypatch):
    project.capture()
    monkeypatch.setenv("SCIPROOF_TEST_SUFFIX", "b")  # the reruns write different output
    first = verify(project.manifest, rerun=True, assume_yes=True)
    second = verify(project.manifest, rerun=True, assume_yes=True)
    assert first.rerun_outputs_dir and second.rerun_outputs_dir
    assert first.rerun_outputs_dir != second.rerun_outputs_dir
    assert (project.root / first.rerun_outputs_dir).is_dir()
    assert (project.root / second.rerun_outputs_dir).is_dir()


def test_a_timed_out_rerun_moves_nothing_and_says_nothing_false(project):
    project.capture()
    original = (project.root / "out" / "exact.txt").read_text()
    _edit_manifest(
        project,
        lambda m: m["command"].update(argv=[sys.executable, "-c", "import time; time.sleep(30)"]),
    )
    report = verify(project.manifest, rerun=True, assume_yes=True, timeout=1)
    assert report.status is Status.INCONCLUSIVE
    assert report.rerun_outputs_dir is None
    assert not list(project.root.glob(".sciproof-rerun-*"))
    assert (project.root / "out" / "exact.txt").read_text() == original
    assert "moved" not in format_report(report)


def test_a_command_that_cannot_start_leaves_no_rerun_directory(project):
    project.capture()
    _edit_manifest(
        project, lambda m: m["command"].update(argv=["this-command-does-not-exist-sciproof"])
    )
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.INCONCLUSIVE
    assert not list(project.root.glob(".sciproof-rerun-*"))


def test_a_rerun_that_reproduces_the_outputs_exactly_leaves_no_directory(project):
    project.capture()
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.PASS
    assert report.rerun_outputs_dir is None
    assert not list(project.root.glob(".sciproof-rerun-*"))


def test_an_output_deleted_by_the_rerun_is_restored(project):
    project.capture()
    original = (project.root / "out" / "exact.txt").read_text()
    code = "import os; os.remove('out/exact.txt')"
    _edit_manifest(project, lambda m: m["command"].update(argv=[sys.executable, "-c", code]))
    verify(project.manifest, rerun=True, assume_yes=True)
    assert (project.root / "out" / "exact.txt").read_text() == original


def test_rerun_detects_a_different_exact_output(project, monkeypatch):
    project.capture()
    monkeypatch.setenv("SCIPROOF_TEST_SUFFIX", "b")
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.FAIL
    assert _status(report, "rerun output out/exact.txt") is Status.FAIL


def test_numeric_tolerance_accepts_small_differences(project, monkeypatch):
    project.capture(rtol=1e-6)
    monkeypatch.setenv("SCIPROOF_TEST_EPS", "1e-9")  # value 42 -> 42.000000001
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert _status(report, "rerun output out/result.json") is Status.PASS
    assert report.status is Status.PASS


def test_numeric_tolerance_rejects_large_differences(project, monkeypatch):
    project.capture(rtol=1e-12)
    monkeypatch.setenv("SCIPROOF_TEST_EPS", "1e-3")
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert _status(report, "rerun output out/result.json") is Status.FAIL
    assert report.status is Status.FAIL


def test_exact_comparison_is_the_default_and_rejects_tiny_numeric_noise(project, monkeypatch):
    project.capture(numeric_outputs=[], outputs=["out/result.json", "out/exact.txt"])
    monkeypatch.setenv("SCIPROOF_TEST_EPS", "1e-12")
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert _status(report, "rerun output out/result.json") is Status.FAIL


def test_changed_exit_code_fails(project, monkeypatch):
    project.capture()
    monkeypatch.setenv("SCIPROOF_TEST_EXIT", "4")
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert _status(report, "exit code") is Status.FAIL
    assert report.status is Status.FAIL


def test_rerun_is_not_attempted_when_inputs_are_unavailable(project):
    project.capture()
    (project.root / "data.json").unlink()
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.INCONCLUSIVE
    assert project.runs() == 1


def test_rerun_is_not_attempted_when_inputs_were_modified(project):
    project.capture()
    (project.root / "data.json").write_text('{"x": 1}')
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.FAIL  # the modified input is itself a failure
    assert project.runs() == 1


def test_missing_command_is_inconclusive(project):
    project.capture()
    _edit_manifest(
        project, lambda m: m["command"]["argv"].__setitem__(0, "no-such-program-sciproof")
    )
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.INCONCLUSIVE
    assert "cannot start" in next(c for c in report.checks if c.subject == "rerun").message


def test_rerun_timeout_is_inconclusive(project):
    project.capture()
    _edit_manifest(
        project,
        lambda m: m["command"].update(argv=[sys.executable, "-c", "import time; time.sleep(30)"]),
    )
    report = verify(project.manifest, rerun=True, assume_yes=True, timeout=0.5)
    assert report.status is Status.INCONCLUSIVE


def test_environment_difference_is_reported_and_strict_mode_is_inconclusive(project):
    project.capture()
    _edit_manifest(project, lambda m: m["environment"]["python"].update(version="2.7.18"))
    lenient = verify(project.manifest, rerun=True, assume_yes=True)
    assert lenient.status is Status.PASS
    assert any("2.7.18" in warning for warning in lenient.warnings)

    runs = project.runs()
    strict = verify(project.manifest, rerun=True, assume_yes=True, strict_env=True)
    assert strict.status is Status.INCONCLUSIVE
    assert project.runs() == runs  # strict mode stops before executing


def test_env_var_set_now_but_not_at_capture_is_an_environment_difference(project, monkeypatch):
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    project.capture()
    monkeypatch.setenv("OMP_NUM_THREADS", "8")

    lenient = verify(project.manifest, rerun=True, assume_yes=True)
    assert any("OMP_NUM_THREADS is set now" in w for w in lenient.warnings)

    runs = project.runs()
    strict = verify(project.manifest, rerun=True, assume_yes=True, strict_env=True)
    assert strict.status is Status.INCONCLUSIVE
    assert project.runs() == runs  # strict mode stops before executing


def test_env_var_set_at_capture_but_not_now_is_an_environment_difference(project, monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "4")
    project.capture()
    monkeypatch.delenv("OMP_NUM_THREADS")
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert any("OMP_NUM_THREADS was set at capture time" in w for w in report.warnings)


def test_missing_dependency_is_reported(project):
    project.capture()
    _edit_manifest(
        project,
        lambda m: m["dependencies"].append({"name": "surely-not-installed-pkg", "version": "9.9"}),
    )
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert any("surely-not-installed-pkg" in w and "not installed" in w for w in report.warnings)


def test_interrupted_rerun_restores_the_originals(project, monkeypatch):
    project.capture()
    original = (project.root / "out" / "exact.txt").read_bytes()

    def interrupted(argv, cwd, timeout):
        (cwd / "out" / "exact.txt").write_text("half-written")
        raise verify_module.CommandInterrupted("interrupted")

    monkeypatch.setattr(verify_module, "run_command", interrupted)
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.INCONCLUSIVE
    assert (project.root / "out" / "exact.txt").read_bytes() == original


@pytest.mark.parametrize(
    "timeout",
    [0, -1, float("nan"), float("inf"), 1e10, MAX_WAIT_SECONDS + 1],
)
def test_invalid_rerun_timeout_is_a_clean_error_and_runs_nothing(project, timeout):
    project.capture()
    runs = project.runs()
    with pytest.raises(UsageError, match="timeout"):
        verify(project.manifest, rerun=True, assume_yes=True, timeout=timeout)
    assert project.runs() == runs


def test_rerun_with_a_huge_json_integer_gives_a_verdict_not_a_traceback(project):
    code = "open('big.json', 'w').write('{\"x\": 1' + '0' * 400 + '}')"
    project.capture(
        argv=[sys.executable, "-c", code],
        inputs=["data.json"],
        outputs=[],
        numeric_outputs=["big.json"],
    )
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.PASS


# ---------------------------------------------------------------- a rerun starts clean


def test_a_stale_output_cannot_make_a_rerun_that_wrote_nothing_pass(project):
    """The old file must not 'reproduce' a result the command never wrote."""
    project.capture()
    data = json.loads(project.manifest.read_text())
    data["command"]["argv"] = [sys.executable, "-c", "pass"]  # writes nothing at all
    project.manifest.write_text(json.dumps(data))
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.FAIL
    messages = {c.subject: c.message for c in report.checks}
    assert messages["rerun output out/exact.txt"] == "the rerun did not produce this file"
    assert messages["rerun output out/result.json"] == "the rerun did not produce this file"


def test_the_recorded_outputs_are_restored_after_a_rerun_that_wrote_nothing(project):
    project.capture()
    before = {p: (project.root / p).read_bytes() for p in ("out/exact.txt", "out/result.json")}
    data = json.loads(project.manifest.read_text())
    data["command"]["argv"] = [sys.executable, "-c", "pass"]
    project.manifest.write_text(json.dumps(data))
    verify(project.manifest, rerun=True, assume_yes=True)
    assert {p: (project.root / p).read_bytes() for p in before} == before


def test_the_command_really_runs_without_its_old_outputs(project):
    """A command that looks for its own previous output sees none, as in a first run."""
    project.capture()
    code = "import pathlib, sys; sys.exit(7 if pathlib.Path('out/exact.txt').exists() else 0)"
    data = json.loads(project.manifest.read_text())
    data["command"]["argv"] = [sys.executable, "-c", code]
    data["execution"]["exit_code"] = 0
    project.manifest.write_text(json.dumps(data))
    report = verify(project.manifest, rerun=True, assume_yes=True)
    exit_check = next(c for c in report.checks if c.subject == "exit code")
    assert exit_check.status is Status.PASS  # exit 0: the file was not there


def test_a_rerun_that_cannot_start_leaves_the_outputs_in_place(project):
    project.capture()
    before = (project.root / "out" / "exact.txt").read_bytes()
    data = json.loads(project.manifest.read_text())
    data["command"]["argv"] = ["this-program-does-not-exist-sciproof"]
    project.manifest.write_text(json.dumps(data))
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.INCONCLUSIVE
    assert (project.root / "out" / "exact.txt").read_bytes() == before


def test_rerun_timeout_over_the_limit_names_the_limit_and_runs_nothing(project):
    project.capture()
    runs = project.runs()
    with pytest.raises(UsageError, match="at most 2147484 seconds"):
        verify(project.manifest, rerun=True, assume_yes=True, timeout=1e10)
    assert project.runs() == runs


def test_rerun_timeout_at_the_limit_is_accepted(project):
    project.capture()
    report = verify(project.manifest, rerun=True, assume_yes=True, timeout=MAX_WAIT_SECONDS)
    assert report.status is Status.PASS
