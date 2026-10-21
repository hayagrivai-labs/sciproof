"""JUnit XML output and --save-json."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET  # noqa: S405  (parsing our own output in a test)

from sciproof import cli
from sciproof.junit import list_junit, report_junit
from sciproof.ledger import Entry
from sciproof.verify import Report, Status, verify


def _suite(xml_text):
    root = ET.fromstring(xml_text)  # noqa: S314
    assert root.tag == "testsuites"
    (suite,) = root
    return suite


def test_a_passing_verification_is_all_passing_test_cases(project):
    project.capture()
    suite = _suite(report_junit(verify(project.manifest)))
    cases = suite.findall("testcase")
    assert int(suite.get("tests")) == len(cases) > 0
    assert (suite.get("failures"), suite.get("skipped"), suite.get("errors")) == ("0", "0", "0")
    assert suite.get("name") == "sciproof:exp"
    assert not suite.findall(".//failure") and not suite.findall(".//skipped")
    properties = {p.get("name"): p.get("value") for p in suite.find("properties")}
    assert properties["result"] == "PASS" and properties["reason"].startswith("PASS because")
    assert "not checked: scientific validity" in suite.find("system-out").text


def test_a_failed_check_is_a_failure_and_an_inconclusive_one_is_skipped_with_its_reason(project):
    project.capture()
    (project.root / "out" / "exact.txt").write_text("tampered")
    (project.root / "data.json").unlink()
    suite = _suite(report_junit(verify(project.manifest)))
    failures = suite.findall(".//failure")
    skipped = suite.findall(".//skipped")
    assert len(failures) == 1 and failures[0].get("type") == "FAIL"
    assert "modified since capture" in failures[0].get("message")
    assert len(skipped) == 1 and skipped[0].get("message").startswith("INCONCLUSIVE: not available")
    assert (suite.get("failures"), suite.get("skipped")) == ("1", "1")


def test_hostile_text_cannot_break_the_xml():
    report = Report(name="x</testsuite><evil/>", rerun_requested=False)
    report.add(Status.FAIL, 'in"put <a&b>', "\x00\x07 <script>alert(1)</script> & \ud800 end")
    xml_text = report_junit(report)
    suite = _suite(xml_text)  # still well-formed
    assert "<script>" not in xml_text and "<evil/>" not in xml_text
    assert suite.find("testcase").get("name") == 'in"put <a&b>'
    assert "\x00" not in xml_text and "\x07" not in xml_text


def test_a_report_without_checks_is_an_empty_suite():
    suite = _suite(report_junit(Report(name="empty", rerun_requested=False)))
    assert suite.get("tests") == "0" and not suite.findall("testcase")


def test_list_junit_has_a_case_per_manifest():
    entries = [
        Entry("a.sciproof.json", verdict="PASS"),
        Entry("b.sciproof.json", verdict="FAIL"),
        Entry("c.sciproof.json", verdict="INCONCLUSIVE"),
        Entry("d.sciproof.json", error="not valid JSON"),
    ]
    suite = _suite(list_junit(entries))
    assert (suite.get("tests"), suite.get("failures"), suite.get("skipped")) == ("4", "2", "1")
    kinds = {c.get("name"): [child.tag for child in c] for c in suite.findall("testcase")}
    assert kinds == {
        "a.sciproof.json": [],
        "b.sciproof.json": ["failure"],
        "c.sciproof.json": ["skipped"],
        "d.sciproof.json": ["failure"],
    }


def test_cli_junit_for_verify_and_list(project, capsys):
    project.capture()
    assert cli.main(["verify", "exp.sciproof.json", "--format", "junit"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert _suite(out).get("failures") == "0"
    assert cli.main(["list", "--check", "--format", "junit"]) == cli.EXIT_OK
    assert _suite(capsys.readouterr().out).get("name") == "sciproof:list"
    (project.root / "out" / "exact.txt").write_text("x")
    assert cli.main(["verify", "exp.sciproof.json", "--format", "junit"]) == cli.EXIT_FAIL
    assert _suite(capsys.readouterr().out).get("failures") == "1"


def test_save_json_writes_the_json_report_whatever_is_printed(project, tmp_path, capsys):
    project.capture()
    target = tmp_path / "report.json"
    assert (
        cli.main(
            ["verify", "exp.sciproof.json", "--format", "markdown", "--save-json", str(target)]
        )
        == 0
    )
    printed = capsys.readouterr().out
    assert printed.startswith("## SciProof: ")
    saved = json.loads(target.read_text())
    assert saved["schema"] == "sciproof.report" and saved["result"] == "PASS"
    assert b"\r\n" not in target.read_bytes()


def test_save_json_is_written_for_a_failing_verification_too(project, tmp_path, capsys):
    project.capture()
    (project.root / "out" / "exact.txt").write_text("x")
    target = tmp_path / "r.json"
    assert cli.main(["verify", "exp.sciproof.json", "--save-json", str(target)]) == cli.EXIT_FAIL
    assert json.loads(target.read_text())["result"] == "FAIL"
