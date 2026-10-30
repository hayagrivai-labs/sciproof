"""``sciproof list`` (the directory is the ledger) and ``sciproof export``."""

from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest

from sciproof import cli
from sciproof.errors import ManifestError, UsageError
from sciproof.export import PREDICATE_TYPE, STATEMENT_TYPE, to_in_toto
from sciproof.ledger import Entry, format_json, format_markdown, list_manifests, worst_verdict

# ---------------------------------------------------------------- list


def test_an_empty_directory_lists_nothing(tmp_path, capsys):
    assert list_manifests(tmp_path) == []
    assert cli.main(["list", str(tmp_path)]) == cli.EXIT_OK
    assert "no manifests found" in capsys.readouterr().out


def test_manifests_are_listed_newest_first_with_their_facts(project):
    project.capture(name="a")
    project.capture(name="b", claims=[("out/result.json", "$.value >= 1")])
    entries = list_manifests(project.root)
    assert [e.name for e in entries] == ["b", "a"]
    b = entries[0]
    assert (b.file, b.exit_code, b.inputs, b.outputs, b.claims) == ("b.sciproof.json", 0, 2, 2, 1)
    assert b.signed is False and b.verdict is None and b.error is None


def test_a_signature_file_is_shown(project):
    project.capture(name="a")
    (project.root / "a.sciproof.json.sig").write_text("x")
    assert list_manifests(project.root)[0].signed is True


def test_an_invalid_manifest_is_listed_as_invalid_not_fatal(project):
    project.capture(name="good")
    (project.root / "bad.sciproof.json").write_text("{not json")
    entries = {e.file: e for e in list_manifests(project.root)}
    assert entries["good.sciproof.json"].error is None
    assert "not valid JSON" in entries["bad.sciproof.json"].error


def test_check_verifies_each_manifest_without_running_anything(project):
    project.capture(name="a")
    project.capture(name="b")
    runs = project.runs()
    (project.root / "out" / "exact.txt").write_text("changed")  # both manifests describe it
    entries = list_manifests(project.root, check=True)
    assert {e.verdict for e in entries} == {"FAIL"}
    assert project.runs() == runs


def test_worst_verdict_orders_fail_over_inconclusive_over_pass():
    ok, bad, unsure = (
        Entry("a", verdict="PASS"),
        Entry("b", verdict="FAIL"),
        Entry("c", verdict="INCONCLUSIVE"),
    )
    assert worst_verdict([ok]) == "PASS"
    assert worst_verdict([ok, unsure]) == "INCONCLUSIVE"
    assert worst_verdict([ok, unsure, bad]) == "FAIL"
    assert worst_verdict([Entry("x", error="invalid")]) == "FAIL"
    assert worst_verdict([Entry("x")]) is None


def test_cli_check_exit_codes(project, capsys):
    project.capture(name="a")
    assert cli.main(["list", "--check"]) == cli.EXIT_OK
    assert "1 PASS" in capsys.readouterr().out
    (project.root / "data.json").unlink()
    assert cli.main(["list", "--check"]) == cli.EXIT_INCONCLUSIVE
    capsys.readouterr()
    (project.root / "bad.sciproof.json").write_text("{}")
    assert cli.main(["list", "--check"]) == cli.EXIT_FAIL
    assert "INVALID" in capsys.readouterr().out
    assert cli.main(["list"]) == cli.EXIT_OK  # without --check it only lists


def test_recursive_listing_finds_nested_manifests_and_skips_hidden_directories(project):
    project.capture(name="top")
    for folder, name in (("sub", "nested"), (".hidden", "secret"), ("node_modules", "dep")):
        (project.root / folder).mkdir()
        (project.root / folder / f"{name}.sciproof.json").write_bytes(
            project.manifest.with_name("top.sciproof.json").read_bytes()
        )
    flat = {e.file for e in list_manifests(project.root)}
    deep = {e.file for e in list_manifests(project.root, recursive=True)}
    assert flat == {"top.sciproof.json"}
    assert deep == {"top.sciproof.json", "sub/nested.sciproof.json"}


def test_manifests_that_are_symlinks_are_not_followed(project):
    project.capture(name="a")
    link = project.root / "link.sciproof.json"
    try:
        os.symlink(project.root / "a.sciproof.json", link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    assert {e.file for e in list_manifests(project.root)} == {"a.sciproof.json"}


def test_a_missing_directory_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(UsageError, match="not a directory"):
        list_manifests(tmp_path / "nope")
    assert cli.main(["list", str(tmp_path / "nope")]) == cli.EXIT_ERROR


def test_too_many_manifests_raise_usage_error(project, monkeypatch):
    monkeypatch.setattr("sciproof.ledger.MAX_MANIFESTS", 2)

    project.capture(name="first")
    project.capture(name="second")
    project.capture(name="third")

    with pytest.raises(UsageError, match="more than 2 manifests"):
        list_manifests(project.root)


def test_json_and_markdown_output(project, capsys):
    project.capture(name="a")
    data = json.loads(format_json(list_manifests(project.root, check=True), True))
    assert data["schema"] == "sciproof.list" and data["checked"] is True
    assert data["result"] == "PASS" and data["manifests"][0]["name"] == "a"
    assert cli.main(["list", "--format", "json"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["result"] is None  # nothing was checked
    assert cli.main(["list", "--format", "markdown", "--check"]) == cli.EXIT_OK
    assert capsys.readouterr().out.startswith("## SciProof manifests")


def test_markdown_escapes_file_names_and_errors():
    entries = [Entry("a|b<script>.sciproof.json", error="<img src=x> | broken")]
    text = format_markdown(entries, False)
    assert "<script>" not in text and "<img" not in text
    assert "a\\|b&lt;script&gt;" in text


# ---------------------------------------------------------------- export


def test_the_statement_has_the_in_toto_v1_shape(project):
    project.capture(claims=[("out/result.json", "$.value >= 1")])
    statement = to_in_toto(project.manifest)
    assert statement["_type"] == STATEMENT_TYPE == "https://in-toto.io/Statement/v1"
    assert statement["predicateType"] == PREDICATE_TYPE
    names = {s["name"]: s["digest"]["sha256"] for s in statement["subject"]}
    assert set(names) == {"out/exact.txt", "out/result.json"}  # outputs are the subjects
    for path, digest in names.items():
        assert digest == hashlib.sha256((project.root / path).read_bytes()).hexdigest()
    json.dumps(statement, allow_nan=False)


def test_the_predicate_describes_the_run_and_says_it_is_unsigned(project):
    project.capture(claims=[("out/result.json", "$.value >= 1")])
    predicate = to_in_toto(project.manifest)["predicate"]
    assert predicate["experiment"] == "exp" and predicate["execution"]["exitCode"] == 0
    assert (
        predicate["manifestDigest"]["sha256"]
        == hashlib.sha256(project.manifest.read_bytes()).hexdigest()
    )
    assert {m["name"] for m in predicate["materials"]} == {"data.json", "experiment.py"}
    assert predicate["claims"] == ["out/result.json: $.value >= 1"]
    assert any("unsigned" in note for note in predicate["notes"])
    assert {o["name"]: o["compare"]["mode"] for o in predicate["outputs"]} == {
        "out/exact.txt": "exact",
        "out/result.json": "numeric",
    }
    assert all("snapshot" not in o["compare"] for o in predicate["outputs"])


SCRIPT = (
    "import pathlib; d = pathlib.Path('results'); d.mkdir(exist_ok=True); "
    "(d / 'a.txt').write_text('a'); (d / 'b.txt').write_text('b')"
)


def test_the_files_of_a_directory_become_separate_subjects(project):
    project.capture(
        argv=[sys.executable, "-c", SCRIPT],
        inputs=["data.json"],
        outputs=["results"],
        numeric_outputs=[],
    )
    subjects = {s["name"] for s in to_in_toto(project.manifest)["subject"]}
    assert subjects == {"results/a.txt", "results/b.txt"}


def test_an_absent_output_is_not_a_subject(project):
    result = project.capture(
        argv=[sys.executable, "-c", "import sys; sys.exit(1)"],
        inputs=["data.json"],
        outputs=["never.txt"],
        numeric_outputs=[],
    )
    assert result.exit_code == 1
    statement = to_in_toto(project.root / "exp.sciproof.json")
    assert statement["subject"] == []
    assert statement["predicate"]["outputs"][0]["present"] is False


def test_exporting_an_invalid_manifest_is_refused(project):
    (project.root / "bad.sciproof.json").write_text("{}")
    with pytest.raises(ManifestError):
        to_in_toto(project.root / "bad.sciproof.json")


def test_cli_export_to_stdout_and_to_a_file(project, capsys, tmp_path):
    project.capture()
    assert cli.main(["export", "exp.sciproof.json"]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["_type"] == STATEMENT_TYPE
    target = tmp_path / "statement.json"
    assert cli.main(["export", "exp.sciproof.json", "-o", str(target)]) == cli.EXIT_OK
    assert f"Wrote {target}" in capsys.readouterr().out
    assert b"\r\n" not in target.read_bytes()  # LF on every system
    assert json.loads(target.read_text())["subject"]
    assert cli.main(["export", "missing.sciproof.json"]) == cli.EXIT_ERROR
