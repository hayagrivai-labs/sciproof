"""Structured numeric differences and the Markdown report."""

from __future__ import annotations

import json
import math
from html import unescape

import pytest

from sciproof import cli
from sciproof.errors import UsageError
from sciproof.markdown import escape, number, report_markdown
from sciproof.numeric import Difference, compare, compare_detailed, load_numeric
from sciproof.verify import Check, Report, Status, report_to_dict, verify


def _leaves(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return load_numeric(path)


# ---------------------------------------------------------------- structured differences


def test_a_numeric_difference_carries_its_numbers(tmp_path):
    recorded = _leaves(tmp_path, "r.json", '{"loss": 0.5}')
    found = _leaves(tmp_path, "f.json", '{"loss": 0.6}')
    (diff,) = compare_detailed(recorded, found, 1e-3, 0.0)
    assert diff.kind == "number"
    assert diff.location == "$.loss"
    assert (diff.recorded, diff.found) == (0.5, 0.6)
    assert diff.abs_diff == pytest.approx(0.1)
    assert diff.rel_diff == pytest.approx(0.1 / 0.6)
    assert diff.allowed == pytest.approx(1e-3 * 0.6)
    assert diff.abs_diff > diff.allowed  # that is why it is a difference


def test_compare_returns_exactly_the_messages_of_compare_detailed(tmp_path):
    recorded = _leaves(tmp_path, "r.json", '{"a": 1.0, "b": "x", "c": [1, 2]}')
    found = _leaves(tmp_path, "f.json", '{"a": 2.0, "b": "y", "c": [1, 3]}')
    assert compare(recorded, found, 0.0, 0.0) == [
        d.message for d in compare_detailed(recorded, found, 0.0, 0.0)
    ]


def test_text_and_structure_differences_have_no_numbers(tmp_path):
    recorded = _leaves(tmp_path, "r.json", '{"label": "ok", "n": [1, 2]}')
    found = _leaves(tmp_path, "f.json", '{"label": "bad", "n": [1]}')
    by_kind = {d.kind: d for d in compare_detailed(recorded, found, 0.0, 0.0)}
    assert by_kind["value"].abs_diff is None
    assert "structure" in by_kind


def test_integers_are_exact_so_nothing_is_allowed(tmp_path):
    recorded = _leaves(tmp_path, "r.json", "[100]")
    found = _leaves(tmp_path, "f.json", "[101]")
    (diff,) = compare_detailed(recorded, found, 0.5, 0.5)
    assert (diff.abs_diff, diff.allowed) == (1.0, 0.0)


def test_non_finite_values_become_strict_json_strings(tmp_path):
    recorded = _leaves(tmp_path, "r.json", "[1.0, NaN]")
    found = _leaves(tmp_path, "f.json", "[Infinity, 1.0]")
    diffs = compare_detailed(recorded, found, 0.0, 0.0)
    assert [d.found for d in diffs] == ["Infinity", 1.0]
    assert diffs[1].recorded == "NaN"
    assert all(d.abs_diff is None for d in diffs)  # undefined for non-finite values
    json.dumps([d.to_dict() for d in diffs], allow_nan=False)  # strictly valid JSON


def test_a_relative_difference_against_zero_is_defined_only_when_both_are_zero():
    d = Difference("m", "number", "x", 0.0, 0.0, 0.0, 0.0, 0.0)
    assert d.to_dict()["rel_diff"] == 0.0


def test_huge_values_are_shortened_in_the_records(tmp_path):
    big = "9" * 400
    recorded = _leaves(tmp_path, "r.json", f"[{big}]")
    found = _leaves(tmp_path, "f.json", "[1]")
    (diff,) = compare_detailed(recorded, found, 0.0, 0.0)
    assert "characters" in str(diff.recorded)
    assert len(json.dumps(diff.to_dict())) < 1000


def test_csv_differences_name_the_column_in_the_record(tmp_path):
    recorded = _leaves(tmp_path, "r.csv", "epoch,loss\n1,0.5\n")
    found = _leaves(tmp_path, "f.csv", "epoch,loss\n1,0.9\n")
    (diff,) = compare_detailed(recorded, found, 1e-9, 0.0)
    assert diff.location == "r1c1 (column 'loss')"
    assert math.isclose(diff.abs_diff, 0.4)


# ---------------------------------------------------------------- evidence in the report


def test_the_rerun_evidence_lists_the_difference_details(project, monkeypatch):
    project.capture(rtol=1e-9)
    monkeypatch.setenv("SCIPROOF_TEST_EPS", "1e-3")
    data = report_to_dict(verify(project.manifest, rerun=True, assume_yes=True))
    evidence = next(c for c in data["checks"] if c["subject"] == "rerun output out/result.json")[
        "evidence"
    ]
    (detail,) = evidence["difference_details"]
    assert detail["location"] == "$.value"
    assert detail["abs_diff"] == pytest.approx(1e-3)
    assert detail["allowed"] < detail["abs_diff"]
    assert evidence["differences"] == [detail["message"]]
    json.dumps(data, allow_nan=False)


def test_a_rerun_within_tolerance_has_no_details(project, monkeypatch):
    project.capture(rtol=1e-6)
    monkeypatch.setenv("SCIPROOF_TEST_EPS", "1e-12")
    data = report_to_dict(verify(project.manifest, rerun=True, assume_yes=True))
    evidence = next(c for c in data["checks"] if c["subject"] == "rerun output out/result.json")[
        "evidence"
    ]
    assert evidence["difference_details"] == []


# ---------------------------------------------------------------- Markdown


def test_markdown_has_the_verdict_the_reason_and_what_was_not_checked(project):
    project.capture()
    text = verify(project.manifest).to_markdown()
    assert text.startswith("## SciProof: :white_check_mark: PASS: exp\n")
    assert "> PASS because all" in text
    assert "| :white_check_mark: PASS | input data.json | unchanged (SHA-256 matches) |" in text
    assert "### Not checked" in text
    assert "- reproducibility: the recorded command was not executed" in text
    assert "### Numeric differences" not in text


def test_markdown_shows_a_numeric_difference_table(project, monkeypatch):
    project.capture(rtol=1e-9)
    monkeypatch.setenv("SCIPROOF_TEST_EPS", "1e-3")
    text = verify(project.manifest, rerun=True, assume_yes=True).to_markdown()
    assert "## SciProof: :x: FAIL: exp" in text
    assert "### Numeric differences: out/result.json" in text
    assert "| Location | Recorded | Found | Abs. diff | Rel. diff | Allowed |" in text
    row = next(line for line in text.splitlines() if line.startswith("| $.value "))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[0] == "$.value"
    assert cells[1] == "42" and cells[2] == "42.001"
    assert "0.001" in cells[3]


def test_markdown_escapes_everything_that_comes_from_a_manifest():
    report = Report(name="x|<b>y</b>", rerun_requested=False)
    report.add(Status.FAIL, "input a|b", "<script>alert(1)</script> [link](http://evil) `code`")
    report.warnings.append("a\nb | c")
    text = report_markdown(report)
    assert "<script>" not in text and "<b>" not in text
    assert "&lt;script&gt;" in text
    assert "[link](http://evil)" not in text  # brackets are escaped, so it is not a link
    assert "a\\|b" in text
    assert "a\\\\nb \\| c" in text  # the rendered line break stays literal \n
    for line in text.splitlines():  # no check can break out of its table row
        assert line.count("|") - line.count("\\|") in (0, 4)


def test_escape_and_number_helpers():
    assert escape("a|b\nc") == "a\\|b\\\\nc"
    assert escape("a  b   c") == "a b c"  # runs of blanks collapse
    assert escape("x" * 1000, limit=10).startswith("xxxxxxxxxx")
    assert number(None) == "-"
    assert number(0.1 + 0.2) == "0.3"
    assert number(True) == "True"
    assert number(12345678.9) == "1.23457e+07"


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/x",
        "www.example.com/x",
        "WWW.example.com/x",
        "ftp://example.com/x",
        "person@example.com",
        "@octocat",
        "#123",
        ":tada:",
        "**strong**",
        "_emphasis_",
        "~~removed~~",
    ],
)
def test_untrusted_gfm_syntax_is_inert_but_keeps_its_visible_text(text):
    escaped = escape(text)
    assert text not in escaped
    assert unescape(escaped).replace("\u200b", "") == text


def test_mentions_references_and_emoji_have_breaks_after_entity_decoding():
    # GitHub post-processes mentions/emoji after parsing character entities.
    # Encoding only @ or : is insufficient; a zero-width break must remain.
    decoded = unescape(escape("@octocat #123 :tada:"))
    assert "@\u200boctocat" in decoded
    assert "#\u200b123" in decoded
    assert ":\u200btada:\u200b" in decoded


@pytest.mark.parametrize("backslashes", [1, 2, 3])
def test_original_backslashes_cannot_reactivate_relative_links(backslashes):
    prefix = "\\" * backslashes
    escaped = escape(f"{prefix}[details{prefix}](notes.md)")
    # Preserve each original slash, then escape the bracket with an odd count.
    protected = "\\" * (backslashes * 2 + 1)
    assert escaped == f"{protected}[details{protected}](notes.md)"


@pytest.mark.parametrize("backslashes", [1, 2, 3])
def test_original_backslashes_and_table_pipes_are_both_preserved(backslashes):
    escaped = escape("a" + "\\" * backslashes + "|b")
    assert escaped == "a" + "\\" * (backslashes * 2 + 1) + "|b"


def test_ordinary_filenames_and_numbers_are_unchanged():
    assert escape("results.json 42 0.125") == "results.json 42 0.125"
    assert unescape(escape("results_v2.json")).replace("\u200b", "") == "results_v2.json"


def test_untrusted_gfm_is_escaped_in_every_report_text_field():
    payload = "@octocat #123 :tada: https://example.com www.example.com **bold**"
    report = Report(name=payload, rerun_requested=False)
    report.add(Status.FAIL, payload, payload)
    report.warnings.append(payload)
    report.info.append(payload)
    text = report_markdown(report)
    assert text.count(escape(payload)) >= 5
    assert payload not in text
    assert ":x: FAIL" in text  # trusted status decorations are unchanged


def test_cli_claim_text_is_inert_and_plain_verification_does_not_rerun(project, capsys):
    payload = "see www.example.com and https://example.com cc @octocat #123 :tada:"
    project.capture(claims=[("out/result.json", f'$.label == "{payload}"')])
    assert project.runs() == 1
    code = cli.main(["verify", "exp.sciproof.json", "--format", "markdown"])
    text = capsys.readouterr().out
    assert code == cli.EXIT_FAIL
    assert escape(payload) in text
    assert payload not in text
    assert project.runs() == 1


# ---------------------------------------------------------------- command line


def test_cli_format_markdown_prints_markdown_and_keeps_the_exit_code(project, capsys):
    project.capture()
    (project.root / "out" / "exact.txt").write_text("tampered")
    code = cli.main(["verify", "exp.sciproof.json", "--format", "markdown"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_FAIL
    assert out.startswith("## SciProof: :x: FAIL: exp")


def test_cli_json_flag_is_still_an_alias_and_conflicts_are_refused(project, capsys):
    project.capture()
    assert cli.main(["verify", "exp.sciproof.json", "--json"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["schema"] == "sciproof.report"
    assert cli.main(["verify", "exp.sciproof.json", "--format", "json"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["result"] == "PASS"
    assert cli.main(["verify", "exp.sciproof.json", "--json", "--format", "text"]) == cli.EXIT_ERROR
    assert "conflicts" in capsys.readouterr().err
    with pytest.raises(UsageError):
        cli._report_format(type("A", (), {"json": True, "format": "markdown"})())


def test_numeric_table_summary_row():
    evidence = {
        "kind": "rerun_output_numeric",
        "path": "out/result.json",
        "rtol": 1e-6,
        "atol": 0.0,
        "difference_details": [{"kind": "summary", "message": "more than 10 differences"}],
    }
    report = Report(
        name="exp",
        rerun_requested=False,
        checks=[Check(Status.FAIL, "output out/result.json", "diff", evidence=evidence)],
    )
    md = report_markdown(report)
    assert "| more than 10 differences | | | | | |" in md


def test_number_fallback_escapes_non_primitive():
    result = number(["custom"])
    assert isinstance(result, str)
    assert "custom" in result
    assert result.startswith("\\[")
