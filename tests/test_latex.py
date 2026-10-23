"""``export --format latex``: numbers for a paper that can only come from verified outputs."""

from __future__ import annotations

import json
import sys

import pytest

from sciproof import cli
from sciproof.errors import SciProofError, UsageError
from sciproof.latex import format_value, to_latex

RESULT = "out/result.json"


def _values(*triples):
    return [tuple(t) for t in triples]


def test_values_become_macros_after_a_passing_verification(project):
    project.capture()
    text = to_latex(
        project.manifest,
        _values(("answer", RESULT, "$.value"), ("tag", RESULT, "$.label")),
    )
    lines = text.splitlines()
    assert lines[0].startswith("% SciProof ") and "integrity verification: PASS" in lines[0]
    assert "\\newcommand{\\answer}{42}" in lines
    assert "\\newcommand{\\tag}{ok}" in lines


def test_nothing_is_written_when_the_output_was_modified(project):
    project.capture()
    (project.root / RESULT).write_text(json.dumps({"value": 99.0, "label": "ok"}))
    with pytest.raises(SciProofError, match="not exporting values.*FAIL"):
        to_latex(project.manifest, _values(("answer", RESULT, "$.value")))


def test_csv_cells_are_read_by_row_and_column(project):
    script = "import pathlib; pathlib.Path('t.csv').write_text('name,acc\\nrun1,0.91\\n')"
    project.capture(
        argv=[sys.executable, "-c", script],
        inputs=[],
        outputs=[],
        numeric_outputs=["t.csv"],
    )
    text = to_latex(project.manifest, _values(("acc", "t.csv", "r1c1"), ("who", "t.csv", "r1c0")))
    assert "\\newcommand{\\acc}{0.91}" in text and "\\newcommand{\\who}{run1}" in text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (42, "42"),
        (0.93, "0.93"),
        (1.5e-5, "$1.5\\times 10^{-5}$"),
        (2.5e8, "$2.5\\times 10^{8}$"),
        (0.0, "0"),
        (float("nan"), "NaN"),
        (float("inf"), "$\\infty$"),
        (True, "true"),
        (None, "null"),
        ("a_b & 50% #1 $x$ {y}", "a\\_b \\& 50\\% \\#1 \\$x\\$ \\{y\\}"),
        ("two\nlines", "two lines"),
        (10**400, "1" + "0" * 400),
    ],
)
def test_values_are_written_as_safe_latex(value, expected):
    assert format_value(value) == expected


@pytest.mark.parametrize("name", ["", "a1", "my_value", "x-y", "a" * 41, "\\def", "über"])
def test_a_macro_name_is_letters_only(project, name):
    project.capture()
    with pytest.raises(UsageError, match="macro name"):
        to_latex(project.manifest, _values((name, RESULT, "$.value")))


def test_names_must_be_unique_and_values_given(project):
    project.capture()
    with pytest.raises(UsageError, match="more than once"):
        to_latex(project.manifest, _values(("a", RESULT, "$.value"), ("a", RESULT, "$.label")))
    with pytest.raises(UsageError, match="at least one"):
        to_latex(project.manifest, [])


@pytest.mark.parametrize(
    ("output", "location", "message"),
    [
        ("data.json", "$.x", "not a declared output"),  # an input
        ("nope.json", "$.x", "not a declared output"),
        ("out/exact.txt", "$.x", "not a location"),  # hashed exactly, not readable as values
        (RESULT, "$..value", "not a location"),
        (RESULT, "r1c1", "not a location"),
        (RESULT, "$.missing", "not present"),
        ("../x.json", "$.x", "outside|'\\.\\.'"),
    ],
)
def test_only_declared_readable_locations_can_be_exported(project, output, location, message):
    project.capture()
    with pytest.raises(SciProofError, match=message):
        to_latex(project.manifest, _values(("v", output, location)))


def test_cli_writes_a_file_and_refuses_to_mix_formats(project, capsys):
    project.capture()
    code = cli.main(
        ["export", project.manifest.name, "--format", "latex", "--value", "answer", RESULT]
        + ["$.value", "-o", "numbers.tex"]
    )
    assert code == cli.EXIT_OK, capsys.readouterr().err
    assert "\\newcommand{\\answer}{42}" in (project.root / "numbers.tex").read_text()
    assert cli.main(["export", project.manifest.name, "--value", "a", RESULT, "$.value"]) == (
        cli.EXIT_ERROR
    )
    assert "only used with --format latex" in capsys.readouterr().err
    assert cli.main(["export", project.manifest.name, "--format", "latex"]) == cli.EXIT_ERROR
    assert "at least one --value" in capsys.readouterr().err
