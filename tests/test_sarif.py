"""Tests for SARIF 2.1.0 output in verification reports."""

from __future__ import annotations

import json

from sciproof import cli
from sciproof.sarif import SARIF_SCHEMA, SARIF_VERSION, format_report_sarif, report_sarif
from sciproof.verify import Check, Report, Status, verify


def test_sarif_pass_report(project, capsys):
    project.capture()
    report = verify(project.manifest)
    sarif = report_sarif(report)

    assert sarif["$schema"] == SARIF_SCHEMA
    assert sarif["version"] == SARIF_VERSION
    assert len(sarif["runs"]) == 1
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"] == "sciproof"
    assert run["results"] == []

    # Check CLI output
    code = cli.main(["verify", "exp.sciproof.json", "--format", "sarif"])
    assert code == cli.EXIT_OK
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["runs"][0]["results"] == []


def test_sarif_fail_report(project, capsys):
    project.capture()
    (project.root / "out" / "exact.txt").write_text("modified")
    report = verify(project.manifest)
    sarif = report_sarif(report)

    results = sarif["runs"][0]["results"]
    assert len(results) >= 1
    fail_res = [r for r in results if r["ruleId"] == "SP001"]
    assert fail_res
    assert fail_res[0]["level"] == "error"
    assert "out/exact.txt" in fail_res[0]["message"]["text"]
    assert fail_res[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == (
        "out/exact.txt"
    )

    code = cli.main(["verify", "exp.sciproof.json", "--format", "sarif"])
    assert code == cli.EXIT_FAIL
    cli_out = json.loads(capsys.readouterr().out)
    assert any(r["ruleId"] == "SP001" for r in cli_out["runs"][0]["results"])


def test_format_report_sarif_helper(project):
    project.capture()
    report = verify(project.manifest)
    text = format_report_sarif(report)
    assert text.endswith("\n")
    assert json.loads(text)["version"] == "2.1.0"


def test_sarif_inconclusive_report():
    report = Report(
        name="exp",
        rerun_requested=False,
        checks=[Check(Status.INCONCLUSIVE, "output out/missing.txt", "missing output")],
    )
    results = report_sarif(report)["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["ruleId"] == "SP002"
    assert results[0]["level"] == "warning"


def test_sarif_warning_report():
    report = Report(
        name="exp",
        rerun_requested=False,
        warnings=["some verification warning"],
    )
    results = report_sarif(report)["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["ruleId"] == "SP003"
    assert results[0]["level"] == "warning"
    assert "some verification warning" in results[0]["message"]["text"]
