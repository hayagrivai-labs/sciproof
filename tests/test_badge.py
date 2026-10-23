"""``sciproof badge``: a static SVG that shows one verification, and nothing from the manifest."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405  (parses only the SVG this module wrote)
from datetime import UTC, datetime

import pytest

from sciproof import cli
from sciproof.badge import render_badge
from sciproof.verify import Status


def _svg(text):
    return ET.fromstring(text)  # noqa: S314  (our own output, not untrusted data)


WHEN = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "colour"),
    [(Status.PASS, "#2ea44f"), (Status.FAIL, "#cf222e"), (Status.INCONCLUSIVE, "#bf8700")],
)
def test_each_verdict_has_its_own_colour_and_text(status, colour):
    svg = render_badge(status, rerun=False, when=WHEN)
    root = _svg(svg)  # well-formed XML
    assert root.tag.endswith("svg")
    assert colour in svg
    assert f"{status.value} \u00b7 integrity \u00b7 2026-10-23" in svg


def test_a_rerun_is_labelled_as_one():
    assert "\u00b7 rerun \u00b7" in render_badge(Status.PASS, rerun=True, when=WHEN)
    assert "\u00b7 integrity \u00b7" in render_badge(Status.PASS, rerun=False, when=WHEN)


def test_the_date_is_the_utc_date_of_the_verification():
    late = datetime(2026, 10, 23, 23, 30, tzinfo=UTC)
    assert "2026-10-23" in render_badge(Status.PASS, rerun=False, when=late)


def test_the_badge_is_wide_enough_for_its_text():
    width = int(_svg(render_badge(Status.INCONCLUSIVE, rerun=True, when=WHEN)).get("width"))
    short = int(_svg(render_badge(Status.PASS, rerun=False, when=WHEN)).get("width"))
    assert width > short > 100


def test_cli_writes_the_badge_and_returns_the_verdict(project, capsys):
    project.capture()
    assert cli.main(["badge", project.manifest.name, "-o", "ok.svg"]) == cli.EXIT_OK
    assert "integrity" in (project.root / "ok.svg").read_text(encoding="utf-8")
    assert "PASS: wrote ok.svg" in capsys.readouterr().out


def test_cli_writes_a_failing_badge_and_still_fails(project):
    project.capture()
    (project.root / "out" / "exact.txt").write_text("changed")
    assert cli.main(["badge", project.manifest.name, "-o", "bad.svg"]) == cli.EXIT_FAIL
    svg = (project.root / "bad.svg").read_text(encoding="utf-8")
    assert "FAIL" in svg and "#cf222e" in svg


def test_cli_rerun_badge_says_rerun(project):
    project.capture()
    assert cli.main(["badge", project.manifest.name, "--rerun", "--yes", "-o", "r.svg"]) == 0
    assert "rerun" in (project.root / "r.svg").read_text(encoding="utf-8")


def test_cli_rerun_without_confirmation_writes_no_badge(project, monkeypatch, capsys):
    project.capture()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["badge", project.manifest.name, "--rerun", "-o", "x.svg"]) == cli.EXIT_ERROR
    assert not (project.root / "x.svg").exists()
    assert "refusing to execute" in capsys.readouterr().err


def test_an_unwritable_path_is_a_clean_error(project, capsys):
    project.capture()
    assert cli.main(["badge", project.manifest.name, "-o", "nodir/b.svg"]) == cli.EXIT_ERROR
    assert "cannot write" in capsys.readouterr().err
