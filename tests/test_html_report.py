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

    # Notes section and scope statement
    assert "<h2>Notes</h2>" in html_content
    assert "recorded command was NOT executed" in html_content

    # Not checked section and items
    assert 'id="not-checked"' in html_content
    assert "<h2>Not checked</h2>" in html_content
    for item in report.not_checked:
        assert item in html_content

    # CLI export
    code = cli.main(["verify", str(project.manifest), "--format", "html"])
    assert code == cli.EXIT_OK
    cli_out = capsys.readouterr().out
    assert "<!DOCTYPE html>" in cli_out
    assert "badge-pass" in cli_out
    assert "<h2>Not checked</h2>" in cli_out


def test_html_report_rerun(project):
    project.capture()
    report = verify(project.manifest, rerun=True, assume_yes=True)
    html_content = report_html(report)

    assert 'id="not-checked"' in html_content
    assert "<h2>Not checked</h2>" in html_content
    assert "stdout and stderr of the rerun" in html_content
    assert "environment reconstruction" in html_content
    assert "other machines or platforms" in html_content
    assert "reproducibility: the recorded command was not executed" not in html_content


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
        warnings=['<img src="x" onerror="alert(2)">'],
    )
    report.info.append('<svg onload="alert(3)">malicious note</svg>')
    html_content = report_html(report)
    assert "&lt;script&gt;" in html_content
    assert "<script>alert" not in html_content
    assert "&amp;" in html_content
    assert "&lt;img" in html_content
    assert '<img src="x"' not in html_content
    assert "&lt;svg" in html_content
    assert '<svg onload="' not in html_content


def test_html_report_visual_tolerance_meters():
    evidence = {
        "kind": "rerun_output_numeric",
        "path": "out/metrics.json",
        "rtol": 1e-4,
        "atol": 1e-6,
        "difference_details": [
            {
                "location": "accuracy",
                "recorded": 0.95,
                "found": 0.94999,
                "abs_diff": 0.00001,
                "rel_diff": 0.0000105,
                "allowed": 0.000095,
            },
            {
                "location": "loss",
                "recorded": 0.05,
                "found": 0.052,
                "abs_diff": 0.002,
                "rel_diff": 0.04,
                "allowed": 0.000005,
            },
        ],
    }
    report = Report(
        name="numeric-exp",
        rerun_requested=True,
        checks=[
            Check(
                Status.FAIL,
                "output out/metrics.json",
                "values differ outside tolerance",
                evidence=evidence,
            )
        ],
    )
    html_content = report_html(report)

    assert "Numeric Comparison: out/metrics.json" in html_content
    assert "Tolerance Meter" in html_content
    assert "gauge-fill" in html_content
    assert "gauge-fill exceeded" in html_content
    assert "slider-group" in html_content
    assert "rtol-slider-0" in html_content
    assert "atol-slider-0" in html_content
    assert "updateTolerance(0)" in html_content


def _numeric_report(rtol, atol, details):
    evidence = {
        "kind": "rerun_output_numeric",
        "path": "out/metrics.json",
        "rtol": rtol,
        "atol": atol,
        "difference_details": details,
    }
    return Report(
        name="numeric-exp",
        rerun_requested=True,
        checks=[Check(Status.FAIL, "output out/metrics.json", "differs", evidence=evidence)],
    )


def test_html_report_sliders_start_at_the_recorded_tolerances():
    details = [
        {
            "location": "$.loss",
            "recorded": 1.0,
            "found": 1.0000001,
            "abs_diff": 1e-7,
            "rel_diff": 1e-7,
            "allowed": 1e-9,
        }
    ]
    html_content = report_html(_numeric_report(1e-9, 0.0, details))
    assert 'id="rtol-slider-0" min="-13" max="0" step="0.01" value="-9.00"' in html_content
    assert 'id="atol-slider-0" min="-13" max="0" step="0.01" value="-13"' in html_content
    assert 'id="rtol-val-0">1.0e-9</span>' in html_content
    assert 'id="atol-val-0">0</span>' in html_content
    assert "rtolExp <= -13 ? 0" in html_content
    assert "atolExp <= -13 ? 0" in html_content


def test_html_report_shows_a_difference_without_a_distance_as_exceeded():
    details = [
        {
            "location": "$.label",
            "recorded": "ok",
            "found": "changed",
            "abs_diff": None,
            "rel_diff": None,
            "allowed": 0.0,
        }
    ]
    html_content = report_html(_numeric_report(1e-9, 0.0, details))
    row = html_content.split('id="row-0-$.label"', 1)[1].split("</tr>", 1)[0]
    assert "n/a" in row
    assert "gauge-fill exceeded" in row
    assert 'style="width: 100.0%"' in row
    assert 'meta-value" style="font-size:0.75rem">n/a</span>' in row
    assert "comparable ? allowed.toExponential(2) : 'n/a'" in html_content
    assert "const exceeded = !comparable || item.abs_diff > allowed;" in html_content
