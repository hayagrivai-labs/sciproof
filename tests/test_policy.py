"""Verification policies (docs/POLICY.md)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from sciproof import cli
from sciproof.policy import (
    MAX_POLICY_BYTES,
    POLICY_VERSION,
    PolicyError,
    apply_policy,
    load_policy,
    parse_policy,
)
from sciproof.verify import Status, report_to_dict, verify

FULL = """\
version = 1

[manifest]
max_age_days = 30
require_exit_code = 0
require_git = true
forbid_dirty_git = true
require_signature = true
min_claims = 1

[outputs]
require_numeric = ["out/result.json"]
max_rtol = 1e-3
max_atol = 1e-6

[rerun]
require = true
require_clean_env = true
"""


def _write(tmp_path, text, name="policy.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _checks(report, rule):
    return [c for c in report.checks if c.subject == f"policy {rule}"]


def _one(report, rule):
    (check,) = _checks(report, rule)
    return check


# ---------------------------------------------------------------- parsing


def test_a_full_policy_is_accepted_and_keeps_its_values():
    policy = parse_policy(FULL, file="p.toml")
    assert policy.file == "p.toml" and len(policy.sha256) == 64
    assert policy.get("manifest", "max_age_days") == 30
    assert policy.get("outputs", "require_numeric") == ["out/result.json"]
    assert policy.get("rerun", "require") is True
    assert policy.get("manifest", "nope") is None
    assert policy.to_dict() == {
        "file": "p.toml",
        "sha256": policy.sha256,
        "version": POLICY_VERSION,
    }


def test_a_policy_with_only_a_version_has_no_rules():
    assert parse_policy("version = 1\n").rules == {}


def test_the_digest_covers_the_exact_text():
    assert parse_policy("version = 1\n").sha256 != parse_policy("version = 1\n# x\n").sha256


def test_a_utf8_bom_is_accepted_and_kept_in_the_digest(tmp_path):
    path = tmp_path / "bom.toml"
    raw = b"\xef\xbb\xbfversion = 1\n"
    path.write_bytes(raw)

    policy = load_policy(path)

    assert policy.rules == {}
    assert policy.sha256 == hashlib.sha256(raw).hexdigest()


def test_a_utf8_bom_does_not_hide_a_toml_error():
    with pytest.raises(PolicyError, match=r"not valid TOML:.*line 2"):
        parse_policy("\ufeffversion = 1\n[manifest\n", file="bom.toml")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "'version = 1' is required"),
        ("version = 2\n", "'version = 1' is required"),
        ("version = true\n", "'version = 1' is required"),
        ('version = "1"\n', "'version = 1' is required"),
        ("version = 1\n[nonsense]\nx = 1\n", "unknown section \\[nonsense\\]"),
        ("version = 1\nmanifest = 3\n", "unknown section|must be a table"),
        ("version = 1\n[manifest]\nmax_age = 3\n", "unknown rule manifest.max_age"),
        ("version = 1\n[manifest]\nmax_age_days = 0\n", "greater than 0"),
        ("version = 1\n[manifest]\nmax_age_days = -3\n", "greater than 0"),
        ("version = 1\n[manifest]\nmax_age_days = true\n", "greater than 0"),
        ("version = 1\n[manifest]\nmax_age_days = inf\n", "finite number"),
        ('version = 1\n[manifest]\nmax_age_days = "30"\n', "finite number"),
        ("version = 1\n[manifest]\nrequire_exit_code = 0.5\n", "whole number"),
        ("version = 1\n[manifest]\nrequire_exit_code = true\n", "whole number"),
        ('version = 1\n[manifest]\nrequire_git = "yes"\n', "true or false"),
        ("version = 1\n[manifest]\nrequire_signature = 1\n", "true or false"),
        ("version = 1\n[manifest]\nmin_claims = -1\n", "whole number >= 0"),
        ("version = 1\n[outputs]\nmax_rtol = -1e-9\n", "0 or more"),
        ("version = 1\n[outputs]\nmax_atol = nan\n", "finite number"),
        ('version = 1\n[outputs]\nrequire_numeric = "a.json"\n', "list of at most"),
        ('version = 1\n[outputs]\nrequire_numeric = ["../x.json"]\n', "'..'"),
        ('version = 1\n[outputs]\nrequire_numeric = ["/etc/x"]\n', "absolute"),
        ('version = 1\n[outputs]\nrequire_numeric = ["a.json", "a.json"]\n', "listed twice"),
        ("version = 1\n[outputs]\nrequire_numeric = [1]\n", "non-empty string"),
        ("version = 1\n[rerun]\nrequire = 1\n", "true or false"),
        ("version = 1\n[manifest\n", "not valid TOML"),
        ("version = 1\nversion = 1\n", "not valid TOML"),
    ],
)
def test_invalid_policies_are_refused_with_a_reason(text, message):
    with pytest.raises(PolicyError, match=message):
        parse_policy(text, file="p.toml")


def test_load_policy_errors(tmp_path):
    with pytest.raises(PolicyError, match="policy file not found"):
        load_policy(tmp_path / "missing.toml")
    big = _write(tmp_path, "version = 1\n# " + "x" * MAX_POLICY_BYTES, "big.toml")
    with pytest.raises(PolicyError, match="larger than"):
        load_policy(big)
    binary = tmp_path / "bin.toml"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(PolicyError, match="cannot read policy"):
        load_policy(binary)
    assert load_policy(_write(tmp_path, FULL)).file == "policy.toml"


def test_an_invalid_policy_stops_everything_before_a_rerun_starts(project, tmp_path):
    project.capture()
    runs = project.runs()
    bad = _write(tmp_path, "version = 1\n[manifest]\nmax_age_days = 0\n")
    with pytest.raises(PolicyError):
        verify(project.manifest, rerun=True, assume_yes=True, policy=bad)
    assert project.runs() == runs  # the recorded command was not executed


# ---------------------------------------------------------------- the rules


def _apply(project, text, *, now=None, **verify_options):
    project.capture(**verify_options.pop("capture", {}))
    report = verify(project.manifest, **verify_options)
    manifest = json.loads(project.manifest.read_text())
    apply_policy(parse_policy(text), manifest, report, now=now)
    return report


def test_a_fresh_manifest_passes_max_age_and_an_old_one_fails(project):
    text = "version = 1\n[manifest]\nmax_age_days = 30\n"
    report = _apply(project, text)
    assert _one(report, "manifest.max_age_days").status is Status.PASS
    later = datetime.now(UTC) + timedelta(days=45)
    old = _apply(project, text, now=later, capture={"force": True})
    check = _one(old, "manifest.max_age_days")
    assert check.status is Status.FAIL and "45.0 days ago" in check.message
    assert check.evidence["limit"] == 30 and check.evidence["kind"] == "policy"


def test_a_manifest_dated_in_the_future_fails_max_age(project):
    earlier = datetime.now(UTC) - timedelta(days=2)
    report = _apply(project, "version = 1\n[manifest]\nmax_age_days = 30\n", now=earlier)
    assert "in the future" in _one(report, "manifest.max_age_days").message


def test_require_exit_code(project, monkeypatch):
    monkeypatch.setenv("SCIPROOF_TEST_EXIT", "3")
    text = "version = 1\n[manifest]\nrequire_exit_code = 0\n"
    check = _one(_apply(project, text), "manifest.require_exit_code")
    assert check.status is Status.FAIL and "exited with 3" in check.message
    ok = _apply(
        project, "version = 1\n[manifest]\nrequire_exit_code = 3\n", capture={"force": True}
    )
    assert _one(ok, "manifest.require_exit_code").status is Status.PASS


def test_git_rules_without_a_repository(project):
    text = "version = 1\n[manifest]\nrequire_git = true\nforbid_dirty_git = true\n"
    report = _apply(project, text)
    assert _one(report, "manifest.require_git").status is Status.FAIL
    assert _one(report, "manifest.forbid_dirty_git").status is Status.INCONCLUSIVE


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_rules_with_a_clean_and_a_dirty_tree(project):
    def git(*args):
        subprocess.run(  # noqa: S603
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],  # noqa: S607
            cwd=project.root,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "x")
    text = "version = 1\n[manifest]\nrequire_git = true\nforbid_dirty_git = true\n"
    clean = _apply(project, text)
    assert _one(clean, "manifest.require_git").status is Status.PASS
    assert _one(clean, "manifest.forbid_dirty_git").status is Status.PASS
    (project.root / "experiment.py").write_text(
        "# changed\n" + (project.root / "experiment.py").read_text()
    )
    dirty = _apply(project, text, capture={"force": True})
    assert _one(dirty, "manifest.forbid_dirty_git").status is Status.FAIL


def test_min_claims(project):
    text = "version = 1\n[manifest]\nmin_claims = 2\n"
    assert _one(_apply(project, text), "manifest.min_claims").status is Status.FAIL
    claims = [("out/result.json", "$.value >= 1"), ("out/result.json", '$.label == "ok"')]
    report = _apply(project, text, capture={"claims": claims, "force": True})
    assert _one(report, "manifest.min_claims").status is Status.PASS


def test_require_numeric_and_the_tolerance_ceilings(project):
    text = (
        "version = 1\n[outputs]\n"
        'require_numeric = ["out/result.json", "out/exact.txt", "nope.json"]\n'
        "max_rtol = 1e-3\nmax_atol = 1e-6\n"
    )
    report = _apply(project, text, capture={"rtol": 1e-6, "atol": 0.0})
    by_path = {c.evidence["path"]: c for c in _checks(report, "outputs.require_numeric")}
    assert by_path["out/result.json"].status is Status.PASS
    assert (
        "exact hash" in by_path["out/exact.txt"].message
        and by_path["out/exact.txt"].status is Status.FAIL
    )
    assert "not a declared output" in by_path["nope.json"].message
    assert _one(report, "outputs.max_rtol").status is Status.PASS
    assert _one(report, "outputs.max_atol").status is Status.PASS
    loose = _apply(
        project, "version = 1\n[outputs]\nmax_rtol = 1e-9\n", capture={"rtol": 1e-6, "force": True}
    )
    check = _one(loose, "outputs.max_rtol")
    assert check.status is Status.FAIL and "out/result.json (rtol=1e-06)" in check.message


def test_a_ceiling_with_no_numeric_output_is_vacuously_met_and_says_so(project):
    report = _apply(
        project,
        "version = 1\n[outputs]\nmax_rtol = 1e-9\n",
        capture={"numeric_outputs": []},
    )
    check = _one(report, "outputs.max_rtol")
    assert check.status is Status.PASS and "nothing to bound" in check.message


def test_rerun_rules(project):
    text = "version = 1\n[rerun]\nrequire = true\nrequire_clean_env = true\n"
    without = _apply(project, text)
    assert _one(without, "rerun.require").status is Status.INCONCLUSIVE
    assert _one(without, "rerun.require_clean_env").status is Status.INCONCLUSIVE
    plain = _apply(project, text, rerun=True, assume_yes=True, capture={"force": True})
    assert _one(plain, "rerun.require").status is Status.PASS
    assert _one(plain, "rerun.require_clean_env").status is Status.FAIL
    clean = _apply(
        project, text, rerun=True, assume_yes=True, clean_env=True, capture={"force": True}
    )
    assert _one(clean, "rerun.require_clean_env").status is Status.PASS


def test_a_declined_rerun_does_not_satisfy_the_rerun_rule(project):
    report = _apply(
        project,
        "version = 1\n[rerun]\nrequire = true\n",
        rerun=True,
        confirm=lambda prompt: False,
    )
    check = _one(report, "rerun.require")
    assert check.status is Status.INCONCLUSIVE and "did not happen" in check.message


# ---------------------------------------------------------------- through verify() and the CLI


def test_verify_applies_the_policy_and_names_it(project, tmp_path):
    project.capture()
    policy = _write(tmp_path, "version = 1\n[manifest]\nrequire_exit_code = 0\nmin_claims = 0\n")
    report = verify(project.manifest, policy=policy)
    assert report.status is Status.PASS
    assert report.policy["file"] == "policy.toml" and len(report.policy["sha256"]) == 64
    data = report_to_dict(report)
    assert data["policy"] == report.policy
    json.dumps(data, allow_nan=False)
    assert report_to_dict(verify(project.manifest))["policy"] is None


def test_a_failing_rule_fails_the_verdict_and_an_unmet_one_is_inconclusive(project, tmp_path):
    project.capture()
    assert (
        verify(
            project.manifest, policy=_write(tmp_path, "version = 1\n[manifest]\nmin_claims = 1\n")
        ).status
        is Status.FAIL
    )
    assert (
        verify(
            project.manifest,
            policy=_write(tmp_path, "version = 1\n[rerun]\nrequire = true\n", "b.toml"),
        ).status
        is Status.INCONCLUSIVE
    )


def test_an_empty_policy_adds_nothing(project, tmp_path):
    project.capture()
    plain = verify(project.manifest)
    report = verify(project.manifest, policy=_write(tmp_path, "version = 1\n"))
    assert len(report.checks) == len(plain.checks) and report.policy is not None


def test_cli_policy_exit_codes(project, tmp_path, capsys):
    project.capture()
    ok = _write(tmp_path, "version = 1\n[manifest]\nrequire_exit_code = 0\n", "ok.toml")
    bad = _write(tmp_path, "version = 1\n[manifest]\nmin_claims = 3\n", "bad.toml")
    unmet = _write(tmp_path, "version = 1\n[rerun]\nrequire = true\n", "unmet.toml")
    assert cli.main(["verify", "exp.sciproof.json", "--policy", str(ok)]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["verify", "exp.sciproof.json", "--policy", str(bad)]) == cli.EXIT_FAIL
    assert "[FAIL] policy manifest.min_claims" in capsys.readouterr().out
    assert (
        cli.main(["verify", "exp.sciproof.json", "--policy", str(unmet)]) == cli.EXIT_INCONCLUSIVE
    )
    capsys.readouterr()
    broken = _write(tmp_path, "version = 9\n", "broken.toml")
    assert cli.main(["verify", "exp.sciproof.json", "--policy", str(broken)]) == cli.EXIT_ERROR
    assert "'version = 1' is required" in capsys.readouterr().err


def test_check_policy_lists_the_rules_or_the_error(tmp_path, capsys):
    assert cli.main(["check-policy", str(_write(tmp_path, FULL))]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "is valid (version 1" in out and "manifest.max_age_days = 30" in out
    assert "outputs.require_numeric = ['out/result.json']" in out
    assert (
        cli.main(["check-policy", str(_write(tmp_path, "version = 1\n", "e.toml"))]) == cli.EXIT_OK
    )
    assert "no rules" in capsys.readouterr().out
    assert cli.main(["check-policy", str(_write(tmp_path, "[x]\n", "x.toml"))]) == cli.EXIT_ERROR
    assert cli.main(["check-policy", str(tmp_path / "nope.toml")]) == cli.EXIT_ERROR


def test_the_example_policy_in_the_repository_is_valid():
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "examples" / "sciproof.policy.toml"
    assert load_policy(example).rules  # and it uses at least one rule


def test_python_is_new_enough_for_tomllib():
    assert sys.version_info >= (3, 11)
