"""Tests for HTML verification report generation."""

from __future__ import annotations

from sciproof import cli
from sciproof.html_report import format_report_html, report_html
from sciproof.verify import Check, Report, Status, verify


def test_html_report_pass(project, capsys):
    project.capture()
    report = verify(project.manifest)
    html_content = report_html(report)

    assert "<!DOCTYPE html>" in html_content
    assert "SciProof" in html_content
    assert "PASS" in html_content
    assert "badge-pass" in html_content
    assert "exp" in html_content
    assert "out/exact.txt" in html_content

    # CLI export
    code = cli.main(["verify", str(project.manifest), "--format", "html"])
    assert code == cli.EXIT_OK
    cli_out = capsys.readouterr().out
    assert "<!DOCTYPE html>" in cli_out
    assert "badge-pass" in cli_out


def test_html_report_fail(project, capsys):
    project.capture()
    (project.root / "out" / "exact.txt").write_text("tampered")
    report = verify(project.manifest)
    html_content = format_report_html(report)

    assert "FAIL" in html_content
    assert "badge-fail" in html_content

    code = cli.main(["verify", str(project.manifest), "--format", "html"])
    assert code == cli.EXIT_FAIL
    assert "badge-fail" in capsys.readouterr().out


def test_html_report_inconclusive_and_warnings():
    report = Report(
        name="exp",
        rerun_requested=False,
        checks=[Check(Status.INCONCLUSIVE, "output out/missing.txt", "missing output")],
        warnings=["Disk space low"],
    )
    html_content = report_html(report)
    assert "badge-inc" in html_content
    assert "INCONCLUSIVE" in html_content
    assert "<h2>Warnings</h2>" in html_content
    assert "<li>Disk space low</li>" in html_content


def test_html_report_escapes_untrusted_input():
    report = Report(
        name="<b>exp</b> & co",
        rerun_requested=False,
        checks=[Check(Status.FAIL, "<script>alert(1)</script>", 'a & "quotes"')],
    )
    html_content = report_html(report)
    assert "&lt;script&gt;" in html_content
    assert "<script>alert" not in html_content
    assert "&amp;" in html_content
