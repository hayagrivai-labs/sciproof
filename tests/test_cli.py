from __future__ import annotations

import json
import os
import subprocess
import sys

from sciproof import __version__, cli


def _capture_args(*extra, name="exp", command=None):
    command = command or [sys.executable, "experiment.py"]
    return [
        "capture",
        "--name",
        name,
        "--input",
        "data.json",
        "--input",
        "experiment.py",
        "--output",
        "out/exact.txt",
        "--output-numeric",
        "out/result.json",
        *extra,
        "--",
        *command,
    ]


def test_version(capsys):
    try:
        cli.main(["--version"])
    except SystemExit as exit_info:
        assert exit_info.code == 0
    assert __version__ in capsys.readouterr().out


def test_capture_verify_inspect_round_trip(project, capsys):
    assert cli.main(_capture_args()) == cli.EXIT_OK
    assert "Evidence written to exp.sciproof.json" in capsys.readouterr().out

    assert cli.main(["verify", "exp.sciproof.json"]) == cli.EXIT_OK
    assert "RESULT: PASS" in capsys.readouterr().out

    assert cli.main(["inspect", "exp.sciproof.json"]) == cli.EXIT_OK
    assert "SciProof manifest: exp" in capsys.readouterr().out


def test_pre_commit_manifest_check_passes_and_detects_tampering(project, capsys):
    """The command used by the pre-commit hook catches changed outputs without rerunning code."""
    cli.main(_capture_args())
    capsys.readouterr()

    hook_command = ["list", "--check", "-r"]
    assert cli.main(hook_command) == cli.EXIT_OK
    assert "PASS" in capsys.readouterr().out

    (project.root / "out" / "exact.txt").write_text("tampered")
    assert cli.main(hook_command) == cli.EXIT_FAIL
    assert "FAIL" in capsys.readouterr().out


def test_verify_exit_codes(project, capsys):
    cli.main(_capture_args())
    capsys.readouterr()

    (project.root / "out" / "exact.txt").write_text("changed")
    assert cli.main(["verify", "exp.sciproof.json"]) == cli.EXIT_FAIL
    assert "RESULT: FAIL" in capsys.readouterr().out

    (project.root / "out" / "exact.txt").write_text("exact-a")
    (project.root / "data.json").unlink()
    assert cli.main(["verify", "exp.sciproof.json"]) == cli.EXIT_INCONCLUSIVE
    assert "RESULT: INCONCLUSIVE" in capsys.readouterr().out


def test_verify_with_rerun_and_yes(project, capsys):
    cli.main(_capture_args())
    capsys.readouterr()
    assert cli.main(["verify", "exp.sciproof.json", "--rerun", "--yes"]) == cli.EXIT_OK
    assert "RESULT: PASS" in capsys.readouterr().out


def test_failing_command_is_recorded_and_reported(project, capsys, monkeypatch):
    monkeypatch.setenv("SCIPROOF_TEST_EXIT", "7")
    assert cli.main(_capture_args()) == cli.EXIT_FAIL
    captured = capsys.readouterr()
    assert "exit code" in captured.out
    assert "failure was recorded" in captured.err
    assert (
        json.loads((project.root / "exp.sciproof.json").read_text())["execution"]["exit_code"] == 7
    )

    assert cli.main(_capture_args("--force", "--allow-nonzero")) == cli.EXIT_OK


def test_capture_without_a_command_is_a_usage_error(project, capsys):
    assert cli.main(["capture", "--name", "x", "--input", "data.json"]) == cli.EXIT_ERROR
    assert "no command given" in capsys.readouterr().err


def test_capture_with_an_unknown_command_is_a_clean_error(project, capsys):
    code = cli.main(_capture_args(command=["no-such-program-sciproof"]))
    assert code == cli.EXIT_ERROR
    assert "cannot start" in capsys.readouterr().err
    assert not (project.root / "exp.sciproof.json").exists()


def test_capture_with_a_missing_input_is_a_clean_error(project, capsys):
    (project.root / "data.json").unlink()
    assert cli.main(_capture_args()) == cli.EXIT_ERROR
    assert "input file not found" in capsys.readouterr().err


def test_missing_and_malformed_manifests_are_clean_errors(project, capsys):
    for command in ("verify", "inspect"):
        assert cli.main([command, "nope.sciproof.json"]) == cli.EXIT_ERROR
        assert "manifest not found" in capsys.readouterr().err

    (project.root / "bad.sciproof.json").write_text("{not json")
    for command in ("verify", "inspect"):
        assert cli.main([command, "bad.sciproof.json"]) == cli.EXIT_ERROR
        assert "not valid JSON" in capsys.readouterr().err


def test_runs_as_a_module(project):
    completed = subprocess.run(
        [sys.executable, "-m", "sciproof", "--version"], capture_output=True, text=True, check=True
    )
    assert __version__ in completed.stdout


def _json_report(capsys):
    return json.loads(capsys.readouterr().out)


def test_verify_json_for_pass_fail_and_inconclusive(project, capsys):
    cli.main(_capture_args())
    capsys.readouterr()

    assert cli.main(["verify", "exp.sciproof.json", "--json"]) == cli.EXIT_OK
    report = _json_report(capsys)
    assert report["result"] == "PASS"
    assert report["name"] == "exp"
    assert {"status", "subject", "message"} <= set(report["checks"][0])
    assert report["rerun_requested"] is False

    (project.root / "out" / "exact.txt").write_text("changed")
    assert cli.main(["verify", "exp.sciproof.json", "--json"]) == cli.EXIT_FAIL
    report = _json_report(capsys)
    assert report["result"] == "FAIL"
    assert any(c["status"] == "FAIL" for c in report["checks"])

    (project.root / "out" / "exact.txt").write_text("exact-a")
    (project.root / "data.json").unlink()
    assert cli.main(["verify", "exp.sciproof.json", "--json"]) == cli.EXIT_INCONCLUSIVE
    assert _json_report(capsys)["result"] == "INCONCLUSIVE"


def test_verify_json_with_rerun_keeps_stdout_pure_json(project, capsys):
    cli.main(_capture_args())
    capsys.readouterr()
    assert cli.main(["verify", "exp.sciproof.json", "--rerun", "--yes", "--json"]) == cli.EXIT_OK
    report = _json_report(capsys)  # would raise if anything but JSON was printed
    assert report["rerun_requested"] is True
    assert report["rerun_outputs_dir"] is None  # identical outputs: nothing was moved aside


def test_verify_json_stays_valid_json_for_special_characters_in_paths(project, capsys):
    cli.main(_capture_args())
    capsys.readouterr()
    manifest = project.root / "exp.sciproof.json"
    data = json.loads(manifest.read_text())
    odd = 'missing "quoted" ' + chr(233) + chr(9731) + ".csv"
    data["inputs"][0]["path"] = odd
    manifest.write_text(json.dumps(data))

    cli.main(["verify", "exp.sciproof.json", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert any(odd in check["subject"] for check in report["checks"])


def test_verify_json_errors_still_go_to_stderr_with_exit_code_3(project, capsys):
    assert cli.main(["verify", "nope.sciproof.json", "--json"]) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "manifest not found" in captured.err


def test_inspect_and_verify_do_not_crash_on_an_unencodable_terminal(project, monkeypatch):
    """A manifest with non-ASCII log text must be printable on an ASCII-only console."""
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")  # let the captured command print Unicode
    command = [sys.executable, "-c", "print('caf\u00e9 \u2713')"]
    code = cli.main(["capture", "--name", "uni", "--input", "data.json", "--", *command])
    assert code == cli.EXIT_OK

    env = {**os.environ, "PYTHONIOENCODING": "ascii"}
    for args in (["inspect"], ["verify"]):
        completed = subprocess.run(
            [sys.executable, "-m", "sciproof", *args, "uni.sciproof.json"],
            capture_output=True,
            text=True,
            env=env,
            cwd=project.root,
        )
        assert completed.returncode == 0, completed.stderr
        assert "Traceback" not in completed.stderr
