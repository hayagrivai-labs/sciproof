"""``sciproof diff OLD NEW``: what changed between two recorded runs."""

from __future__ import annotations

import hashlib
import json
import sys

import pytest

import sciproof
from sciproof import cli
from sciproof.diffmanifests import MAX_LINES_PER_SECTION, diff_manifests, format_diff_json
from sciproof.errors import ManifestError


def _by_section(report, section):
    return [c for c in report.changes if c.section == section]


def _edit(path, mutate):
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def _mark_output_absent(data):
    for output in data["outputs"]:
        if output["path"] == "out/exact.txt":
            output.update(present=False, sha256=None, size=0)


@pytest.fixture
def pair(project):
    """Capture ``a``, then a second run ``b`` with whatever the test changed in between."""

    def make(*, between=None, rtol=1e-9, **overrides):
        first = project.capture(name="a", rtol=rtol)
        if between:
            between()
        second = project.capture(name="b", rtol=rtol, **overrides)
        return first.manifest_path, second.manifest_path

    return make


# ---------------------------------------------------------------- what is and is not a difference


def test_two_identical_runs_have_no_difference(project, pair):
    old, new = pair()
    report = diff_manifests(old, new)
    assert report.identical
    assert report.changes == []
    assert report.summary.startswith("no differences")
    assert report.to_text().splitlines()[-1] == "RESULT: IDENTICAL"


def test_nothing_is_executed(project, pair):
    old, new = pair()
    runs = project.runs()
    diff_manifests(old, new)
    assert project.runs() == runs


def test_a_changed_input_and_the_output_it_caused_are_both_reported(project, pair):
    old, new = pair(between=lambda: (project.root / "data.json").write_text('{"x": 22}'))
    report = diff_manifests(old, new)
    (inp,) = [c for c in _by_section(report, "inputs") if c.subject == "data.json"]
    assert inp.kind == "changed" and "content changed" in inp.message
    out = next(c for c in _by_section(report, "outputs") if c.subject == "out/result.json")
    assert "outside the baseline tolerance (rtol=1e-09, atol=0.0)" in out.message
    assert "$.value: recorded 42.0, found 44.0" in out.message
    assert out.evidence["numeric"]["equivalent"] is False
    detail = out.evidence["numeric"]["difference_details"][0]
    assert detail["abs_diff"] == pytest.approx(2.0)
    assert not _by_section(report, "command") and not _by_section(report, "environment")
    assert report.counts["inputs"] == 1


def test_a_numeric_change_inside_the_tolerance_is_called_equivalent(project, pair, monkeypatch):
    def tiny_change():
        monkeypatch.setenv("SCIPROOF_TEST_EPS", "1e-12")

    old, new = pair(between=tiny_change, rtol=1e-6)
    out = next(c for c in diff_manifests(old, new).changes if c.subject == "out/result.json")
    assert "numerically equivalent within the baseline tolerance" in out.message
    assert out.evidence["numeric"]["equivalent"] is True


def test_the_baseline_tolerance_is_the_one_from_the_old_manifest(project, pair, monkeypatch):
    monkeypatch.setenv("SCIPROOF_TEST_EPS", "1e-3")
    first = project.capture(name="a", rtol=1e-9)
    monkeypatch.setenv("SCIPROOF_TEST_EPS", "0")
    second = project.capture(name="b", rtol=1e-1)  # the new manifest is looser
    out = next(
        c
        for c in diff_manifests(first.manifest_path, second.manifest_path).changes
        if c.subject == "out/result.json"
    )
    assert "outside the baseline tolerance (rtol=1e-09" in out.message  # 1e-3 > 1e-9 * 42
    assert "comparison changed: numeric (rtol=1e-09, atol=0.0) -> numeric (rtol=0.1" in out.message


def test_an_exact_output_that_changed_is_reported_by_hash(project, pair, monkeypatch):
    old, new = pair(between=lambda: monkeypatch.setenv("SCIPROOF_TEST_SUFFIX", "other"))
    change = next(c for c in diff_manifests(old, new).changes if c.subject == "out/exact.txt")
    assert change.kind == "changed" and "content changed (sha256 " in change.message


# ---------------------------------------------------------------- command, environment, git


def test_a_different_command_is_reported(project, pair):
    old, new = pair(argv=[sys.executable, "-O", "experiment.py"])
    (change,) = _by_section(diff_manifests(old, new), "command")
    assert change.subject == "argv" and " -> " in change.message and "-O" in change.message


def test_environment_variable_changes_are_reported_in_both_directions(project, pair, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    first = project.capture(name="a")
    monkeypatch.delenv("TZ")
    monkeypatch.setenv("OMP_NUM_THREADS", "4")
    second = project.capture(name="b")
    changes = {
        c.subject: c
        for c in _by_section(
            diff_manifests(first.manifest_path, second.manifest_path), "environment"
        )
    }
    assert changes["variable TZ"].kind == "removed"
    assert changes["variable OMP_NUM_THREADS"].kind == "added"


@pytest.mark.parametrize("field", ["system", "machine", "release"])
def test_changed_platform_fields_are_reported(project, pair, field):
    old, new = pair()
    _edit(old, lambda d: d["environment"]["platform"].update({field: "before"}))
    _edit(new, lambda d: d["environment"]["platform"].update({field: "after"}))

    report = diff_manifests(old, new)
    (change,) = _by_section(report, "environment")
    assert change.kind == "changed"
    assert change.subject == f"platform {field}"
    assert "before" in change.message and "after" in change.message
    assert not report.identical


@pytest.mark.parametrize(
    ("old_vars", "new_vars", "kind"),
    [
        ({}, {"OMP_NUM_THREADS": "4"}, "added"),
        ({"OMP_NUM_THREADS": "2"}, {"OMP_NUM_THREADS": "4"}, "changed"),
    ],
)
def test_added_and_changed_environment_variable_values_are_reported(
    project, pair, old_vars, new_vars, kind
):
    old, new = pair()
    _edit(old, lambda d: d["environment"].update(env_vars=old_vars))
    _edit(new, lambda d: d["environment"].update(env_vars=new_vars))

    report = diff_manifests(old, new)
    (change,) = _by_section(report, "environment")
    assert change.kind == kind
    assert change.subject == "variable OMP_NUM_THREADS"
    assert "4" in change.message
    if kind == "changed":
        assert "2" in change.message
    assert not report.identical


def test_package_and_python_differences(project, pair):
    old, new = pair()

    def mutate(data):
        data["environment"]["python"]["version"] = "3.0.0"
        data["dependencies"] = [
            {"name": "NumPy", "version": "1.0"},
            {"name": "new-pkg", "version": "2"},
        ]

    _edit(new, mutate)
    _edit(
        old,
        lambda d: d.update(
            dependencies=[{"name": "numpy", "version": "2.0"}, {"name": "gone", "version": "1"}]
        ),
    )
    changes = {c.subject: c for c in _by_section(diff_manifests(old, new), "environment")}
    assert changes["python version"].message.endswith("-> 3.0.0")
    assert changes["package NumPy"].kind == "changed"  # the name is matched case-insensitively
    assert changes["package NumPy"].message == "2.0 -> 1.0"
    assert changes["package new-pkg"].kind == "added"
    assert changes["package gone"].kind == "removed"


def test_git_commit_and_dirty_state(project, pair):
    old, new = pair()
    _edit(old, lambda d: d.update(git={"commit": "a" * 40, "branch": "main", "dirty": False}))
    _edit(new, lambda d: d.update(git={"commit": "b" * 40, "branch": "main", "dirty": True}))
    changes = {c.subject: c.message for c in _by_section(diff_manifests(old, new), "git")}
    assert changes["commit"] == "aaaaaaaaaaaa -> bbbbbbbbbbbb"
    assert changes["working tree"] == "clean -> uncommitted changes"
    _edit(new, lambda d: d.update(git=None))
    (change,) = _by_section(diff_manifests(old, new), "git")
    assert change.message == "recorded in one manifest only"


def test_exit_code_and_output_streams(project, pair, monkeypatch):
    old, new = pair(between=lambda: monkeypatch.setenv("SCIPROOF_TEST_EXIT", "3"))
    # a failed command is still recorded, so the second capture writes a manifest
    changes = {c.subject: c.message for c in _by_section(diff_manifests(old, new), "execution")}
    assert changes["exit code"] == "0 -> 3"


# ---------------------------------------------------------------- declared files and directories


def test_files_declared_in_one_manifest_only(project, pair):
    old, new = pair(inputs=["data.json"])  # experiment.py is no longer declared in b
    (change,) = [c for c in _by_section(diff_manifests(old, new), "inputs") if c.kind == "removed"]
    assert change.subject == "experiment.py"


def test_a_file_that_became_a_directory(project, tmp_path):
    first = project.capture(name="a")
    (project.root / "results").mkdir()
    (project.root / "results" / "f.txt").write_text("x")
    second = project.capture(
        name="b",
        argv=[sys.executable, "-c", "pass"],
        inputs=["data.json", "results"],
        outputs=[],
        numeric_outputs=[],
    )
    removed = {
        c.subject
        for c in diff_manifests(first.manifest_path, second.manifest_path).changes
        if c.kind == "added"
    }
    assert "results" in removed


@pytest.mark.parametrize("section", ["inputs", "outputs"])
def test_a_file_replaced_by_a_directory_is_reported(project, section):
    artifact = project.root / "artifact"
    artifact.write_text("before")
    options = {
        "argv": [sys.executable, "-c", "pass"],
        "inputs": [],
        "outputs": [],
        "numeric_outputs": [],
        section: ["artifact"],
    }
    first = project.capture(name="a", **options)
    artifact.unlink()
    artifact.mkdir()
    (artifact / "result.txt").write_text("after")
    second = project.capture(name="b", **options)

    report = diff_manifests(first.manifest_path, second.manifest_path)
    (change,) = _by_section(report, section)
    assert change.subject == "artifact"
    assert change.kind == "changed"
    assert "was a file" in change.message and "is now a directory" in change.message
    assert not report.identical


def test_directory_trees_are_compared_by_file(project):
    (project.root / "inputs").mkdir()
    (project.root / "inputs" / "x.csv").write_text("1")
    (project.root / "inputs" / "y.csv").write_text("2")
    first = project.capture(name="a", inputs=["data.json", "inputs"])
    (project.root / "inputs" / "x.csv").write_text("changed")
    (project.root / "inputs" / "z.csv").write_text("new")
    second = project.capture(name="b", inputs=["data.json", "inputs"])
    change = next(
        c
        for c in diff_manifests(first.manifest_path, second.manifest_path).changes
        if c.subject == "inputs"
    )
    assert change.message == "tree changed: 1 modified (x.csv); 1 added (z.csv)"
    assert change.evidence["tree"]["modified"] == ["x.csv"]


def test_an_unusable_snapshot_is_said_not_guessed(project, pair):
    old, new = pair(between=lambda: (project.root / "data.json").write_text('{"x": 5}'))
    (project.root / "a.sciproof.d" / "1-result.json").write_text("{}")  # tampered baseline copy
    out = next(c for c in diff_manifests(old, new).changes if c.subject == "out/result.json")
    assert "numeric comparison not possible" in out.message and "was modified" in out.message
    assert out.evidence["numeric"]["comparable"] is False


def test_a_missing_snapshot_is_said_too(project, pair):
    old, new = pair(between=lambda: (project.root / "data.json").write_text('{"x": 5}'))
    (project.root / "b.sciproof.d" / "1-result.json").unlink()
    out = next(c for c in diff_manifests(old, new).changes if c.subject == "out/result.json")
    assert "is missing" in out.message


def test_corrupted_numeric_snapshot_reports_numeric_not_comparable(project, pair):
    old, new = pair(between=lambda: (project.root / "data.json").write_text('{"x": 10}'))
    snapshot = project.root / "a.sciproof.d" / "1-result.json"
    snapshot.write_text("not-a-valid-json-or-csv-{{{")
    # Corrupting the snapshot alone would trip the integrity ("was modified")
    # branch before parsing, so re-record the corrupted snapshot's digest in
    # the old manifest: this is the snapshot load_numeric actually fails on.
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()

    def _rerecord(manifest):
        for item in manifest["outputs"]:
            if item["path"] == "out/result.json":
                item["compare"]["snapshot"]["sha256"] = digest

    _edit(old, _rerecord)
    out = next(c for c in diff_manifests(old, new).changes if c.subject == "out/result.json")
    assert "numeric comparison not possible" in out.message
    assert "is not valid JSON" in out.message
    assert out.evidence["numeric"]["comparable"] is False


# ---------------------------------------------------------------- output formats


def test_text_output_groups_by_section_and_ends_with_a_result(project, pair):
    old, new = pair(between=lambda: (project.root / "data.json").write_text('{"x": 22}'))
    text = diff_manifests(old, new).to_text()
    assert text.startswith("SciProof diff: 'a' -> 'b'")
    assert "  inputs (1)" in text and "  outputs (1)" in text
    assert "    ~ data.json: content changed" in text
    assert "summary: 2 differences (inputs 1, outputs 1)" in text
    assert "possible explanation of a changed output, not proof" in text
    assert text.splitlines()[-1] == "RESULT: DIFFERENT"


def test_json_is_strict_and_versioned(project, pair):
    old, new = pair(between=lambda: (project.root / "data.json").write_text('{"x": 22}'))
    text = format_diff_json(diff_manifests(old, new))
    data = json.loads(text, parse_constant=lambda c: pytest.fail(f"non-strict JSON constant {c}"))
    assert data["schema"] == "sciproof.diff" and data["schema_version"] == "1.0"
    assert data["identical"] is False
    assert data["old"] == {"name": "a", "file": "a.sciproof.json"}  # file names, not full paths
    assert data["counts"]["inputs"] == 1
    assert {c["section"] for c in data["changes"]} == {"inputs", "outputs"}


def test_markdown_escapes_manifest_text(project, pair):
    old, new = pair()
    # the number of installed packages must not matter: both manifests list none, then one is added
    _edit(old, lambda d: d.update(dependencies=[]))
    _edit(
        new,
        lambda d: d.update(dependencies=[{"name": "x|<img src=1>", "version": "[a](http://e)"}]),
    )
    text = diff_manifests(old, new).to_markdown()
    assert text.startswith("## SciProof diff: DIFFERENT: a -> b")
    assert "<img" not in text and "[a](http://e)" not in text
    assert "x\\|&lt;img" in text


def test_long_sections_are_cut_in_text_but_complete_in_json(project, pair):
    old, new = pair()
    packages = [{"name": f"pkg{i:03}", "version": "1"} for i in range(MAX_LINES_PER_SECTION + 5)]
    _edit(new, lambda d: d.update(dependencies=packages))
    report = diff_manifests(old, new)
    assert "... and" in report.to_text()
    assert len(report.to_dict()["changes"]) >= len(packages)


def test_markdown_caps_long_sections_and_reports_the_omitted_count(project, pair):
    old, new = pair()
    packages = [{"name": f"pkg{i:03}", "version": "1"} for i in range(MAX_LINES_PER_SECTION + 5)]
    _edit(old, lambda d: d.update(dependencies=[]))
    _edit(new, lambda d: d.update(dependencies=packages))

    report = diff_manifests(old, new)
    text = report.to_markdown()
    assert f"package pkg{MAX_LINES_PER_SECTION - 1:03}" in text
    assert f"package pkg{MAX_LINES_PER_SECTION:03}" not in text
    assert "... and 5 more" in text
    assert len(_by_section(report, "environment")) == len(packages)


# ---------------------------------------------------------------- command line and API


def test_cli_exit_codes_and_formats(project, pair, capsys):
    old, new = pair()
    assert cli.main(["diff", old.name, new.name]) == cli.EXIT_OK
    assert "RESULT: IDENTICAL" in capsys.readouterr().out
    (project.root / "data.json").write_text('{"x": 22}')
    third = project.capture(name="c").manifest_path
    assert cli.main(["diff", old.name, third.name]) == cli.EXIT_FAIL
    assert "RESULT: DIFFERENT" in capsys.readouterr().out
    assert cli.main(["diff", old.name, third.name, "--format", "json"]) == cli.EXIT_FAIL
    assert json.loads(capsys.readouterr().out)["schema"] == "sciproof.diff"
    assert cli.main(["diff", old.name, third.name, "--json"]) == cli.EXIT_FAIL
    assert json.loads(capsys.readouterr().out)["identical"] is False
    assert cli.main(["diff", old.name, third.name, "--format", "markdown"]) == cli.EXIT_FAIL
    assert capsys.readouterr().out.startswith("## SciProof diff: DIFFERENT")


def test_cli_errors(project, pair, capsys):
    old, _ = pair()
    assert cli.main(["diff", old.name, "missing.sciproof.json"]) == cli.EXIT_ERROR
    assert "manifest not found" in capsys.readouterr().err
    assert (
        cli.main(["diff", old.name, old.name, "--json", "--format", "markdown"]) == cli.EXIT_ERROR
    )
    assert "conflicts" in capsys.readouterr().err


def test_an_invalid_manifest_is_refused(project, pair):
    old, new = pair()
    _edit(new, lambda d: d.update(schema="other"))
    with pytest.raises(ManifestError):
        diff_manifests(old, new)


def test_the_python_api_exports_the_diff(project, pair):
    old, new = pair()
    report = sciproof.diff_manifests(old, new)
    assert isinstance(report, sciproof.DiffReport)
    assert report.identical


def test_output_present_to_absent_is_reported(project, pair):
    old, new = pair()
    _edit(new, _mark_output_absent)
    change = next(c for c in diff_manifests(old, new).changes if c.subject == "out/exact.txt")
    assert "was present, is now absent" in change.message


def test_output_absent_to_present_is_reported(project, pair):
    old, new = pair()
    _edit(old, _mark_output_absent)
    change = next(c for c in diff_manifests(old, new).changes if c.subject == "out/exact.txt")
    assert "was absent, is now present" in change.message


def test_snapshot_unsafe_path(tmp_path):
    from sciproof.diffmanifests import _snapshot
    item = {"compare": {"snapshot": {"path": "../escape.json", "sha256": "0" * 64}}}
    path, reason = _snapshot(tmp_path, item)
    assert path is None
    assert reason is not None and "unsafe snapshot path" in reason
