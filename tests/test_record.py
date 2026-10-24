"""``sciproof record``: capture without declaring files; the runs decide what is stable."""

from __future__ import annotations

import json
import sys

import pytest

from sciproof import cli, load_manifest, verify_manifest
from sciproof.errors import CaptureError
from sciproof.record import record
from sciproof.verify import Status

# Runs are counted in a file outside the project (so the counter is not itself an output).
SCRIPT = r"""
import json, os, pathlib, random

with open(os.environ["COUNT_FILE"], "a") as handle:
    handle.write("x\n")
n = len(open(os.environ["COUNT_FILE"]).read().splitlines())
data = json.loads(pathlib.Path("data.json").read_text())
pathlib.Path("out").mkdir(exist_ok=True)
pathlib.Path("out/stable.txt").write_text("stable")
json.dump({"v": data["x"] * 2}, open("out/stable.json", "w"))
if os.environ.get("NOISE"):
    json.dump({"loss": 1.0 + n * 1e-7, "label": "ok"}, open("out/noisy.json", "w"))
if os.environ.get("JUMP"):
    json.dump({"v": n % 2}, open("out/jump.json", "w"))
if os.environ.get("RANDOM_TEXT"):
    pathlib.Path("out/random.txt").write_text(str(random.random()))
if os.environ.get("RANDOM_JSON"):
    json.dump({"label": str(random.random())}, open("out/label.json", "w"))
if os.environ.get("ONLY_FIRST") and n == 1:
    pathlib.Path("out/first.txt").write_text("1")
"""


@pytest.fixture
def lab(tmp_path, monkeypatch):
    root = tmp_path / "lab"
    root.mkdir()
    (root / "data.json").write_text('{"x": 21}')
    (root / "run.py").write_text(SCRIPT)
    (root / "unrelated.txt").write_text("not read")
    counter = tmp_path / "count.txt"
    monkeypatch.setenv("COUNT_FILE", str(counter))
    for name in ("NOISE", "JUMP", "RANDOM_TEXT", "RANDOM_JSON", "ONLY_FIRST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(root)

    class Lab:
        path = root

        @staticmethod
        def runs():
            return len(counter.read_text().splitlines()) if counter.exists() else 0

        @staticmethod
        def go(**kwargs):
            return record(name="exp", argv=[sys.executable, "run.py"], base=root, **kwargs)

    return Lab


def test_inputs_and_outputs_are_found_without_declaring_anything(lab):
    result = lab.go()
    assert result.inputs == ["data.json", "run.py"]  # unrelated.txt was never read
    assert result.exact == ["out/stable.txt"]
    assert result.numeric == ["out/stable.json"]
    assert result.excluded == []
    manifest = load_manifest(result.capture.manifest_path)
    assert [i["path"] for i in manifest["inputs"]] == ["data.json", "run.py"]
    assert {o["path"]: o["compare"]["mode"] for o in manifest["outputs"]} == {
        "out/stable.txt": "exact",
        "out/stable.json": "numeric",
    }
    assert lab.runs() == 3  # one to look, one to compare, one to capture


def test_the_recorded_evidence_verifies_and_reruns(lab):
    result = lab.go()
    assert verify_manifest(result.capture.manifest_path).status is Status.PASS
    rerun = verify_manifest(result.capture.manifest_path, rerun=True, assume_yes=True)
    assert rerun.status is Status.PASS


def test_a_numeric_output_that_varies_gets_a_tolerance_that_covers_it(lab, monkeypatch):
    monkeypatch.setenv("NOISE", "1")
    result = lab.go()
    assert "out/noisy.json" in result.numeric
    rtol = result.tolerances["out/noisy.json"]["rtol"]
    assert 1e-9 < rtol < 1e-3
    compare = {
        o["path"]: o["compare"] for o in load_manifest(result.capture.manifest_path)["outputs"]
    }
    assert compare["out/noisy.json"]["rtol"] == rtol
    assert compare["out/stable.json"]["rtol"] == 1e-9
    assert "--tolerance out/noisy.json" in result.command_line


def test_a_numeric_output_that_needs_rtol_1_or_more_is_not_declared(lab, monkeypatch):
    # out/jump.json's value jumps between 0 and 1 across the two runs; ten times that spread
    # is rtol=10, which would make any number "equivalent" to any other (issue #123).
    monkeypatch.setenv("JUMP", "1")
    result = lab.go()
    assert "out/jump.json" not in result.numeric
    excluded = dict(result.excluded)
    assert "out/jump.json" in excluded
    assert "meaningful tolerance" in excluded["out/jump.json"]
    assert all(settings["rtol"] < 1 for settings in result.tolerances.values())
    manifest = load_manifest(result.capture.manifest_path)
    assert "out/jump.json" not in [o["path"] for o in manifest["outputs"]]


def test_an_output_that_differs_between_identical_runs_is_not_declared(lab, monkeypatch):
    monkeypatch.setenv("RANDOM_TEXT", "1")
    result = lab.go()
    assert [p for p, _ in result.excluded] == ["out/random.txt"]
    assert "not JSON/CSV/TSV" in result.excluded[0][1]
    manifest = load_manifest(result.capture.manifest_path)
    assert "out/random.txt" not in [o["path"] for o in manifest["outputs"]]


def test_a_json_output_whose_text_changes_is_not_declared(lab, monkeypatch):
    monkeypatch.setenv("RANDOM_JSON", "1")
    result = lab.go()
    assert [p for p, _ in result.excluded] == ["out/label.json"]
    assert "not the same value" in result.excluded[0][1]


def test_an_output_written_by_one_run_only_is_not_declared(lab, monkeypatch):
    monkeypatch.setenv("ONLY_FIRST", "1")
    result = lab.go()
    assert [p for p, _ in result.excluded] == ["out/first.txt"]
    assert "not written by the second run" in result.excluded[0][1]


def test_a_file_read_and_written_by_the_command_is_not_declared(lab):
    # cache.json is state: the command reads it, increments a counter and writes it back, so
    # it cannot be a reproducible output (issue #123).
    (lab.path / "cache.json").write_text('{"n": 0}')
    script = (
        "import json\n"
        "c = json.load(open('cache.json'))\n"
        "c['n'] += 1\n"
        "json.dump(c, open('cache.json', 'w'))\n"
        "json.dump({'v': 1}, open('out.json', 'w'))\n"
    )
    result = record(name="e", argv=[sys.executable, "-c", script], base=lab.path)
    excluded = dict(result.excluded)
    assert "cache.json" in excluded, (
        "cache.json was read and written by the command but was not excluded; it was declared "
        f"as numeric={result.numeric!r} with tolerances={result.tolerances!r}"
    )
    assert "read and written" in excluded["cache.json"]
    assert "cache.json" not in result.inputs
    assert "cache.json" not in result.numeric


def test_fast_skips_the_second_run_and_says_so(lab):
    result = lab.go(fast=True)
    assert lab.runs() == 2
    assert any("--fast" in n for n in result.notes)
    assert result.excluded == []


def test_the_equivalent_capture_command_is_printed(lab):
    line = lab.go().command_line
    assert line.startswith("sciproof capture --name exp --input data.json --input run.py")
    assert "--output out/stable.txt" in line and "--output-numeric out/stable.json" in line
    assert line.endswith("run.py")


def test_exclude_and_extra_inputs(lab):
    (lab.path / "extra.txt").write_text("hand")
    result = lab.go(inputs=["extra.txt"], exclude=["out/stable.txt"])
    assert "extra.txt" in result.inputs
    assert "out/stable.txt" not in result.exact


def test_files_in_ignored_directories_are_not_outputs(lab):
    script = (
        "import pathlib, json; pathlib.Path('.venv').mkdir(exist_ok=True);"
        "pathlib.Path('.venv/x').write_text('1'); json.dump({}, open('r.json', 'w'))"
    )
    result = record(name="e", argv=[sys.executable, "-c", script], base=lab.path)
    assert result.numeric == ["r.json"]


def test_a_command_that_writes_nothing_cannot_be_recorded(lab):
    with pytest.raises(CaptureError, match="created or changed no file"):
        record(name="e", argv=[sys.executable, "-c", "pass"], base=lab.path)


def test_a_failing_command_is_not_recorded(lab):
    with pytest.raises(CaptureError, match="exited with status 3"):
        record(name="e", argv=[sys.executable, "-c", "raise SystemExit(3)"], base=lab.path)
    assert not (lab.path / "e.sciproof.json").exists()


def test_an_existing_manifest_stops_it_before_anything_runs(lab):
    lab.go()
    before = lab.runs()
    with pytest.raises(CaptureError, match="already exists"):
        lab.go()
    assert lab.runs() == before
    assert lab.go(force=True)


def test_a_command_that_is_not_python_gets_a_note_and_keeps_explicit_inputs(lab):
    script = "import json; json.dump({'a': 1}, open('o.json', 'w'))"
    result = record(
        name="e",
        argv=[sys.executable, "-I", "-c", script],  # -I: the audit hook cannot start
        base=lab.path,
        inputs=["data.json"],
    )
    assert result.inputs == ["data.json"]
    assert any("no input could be detected" in n for n in result.notes)


def test_no_leftover_files_of_the_probe_runs_are_declared_or_missing(lab):
    result = lab.go()
    assert (lab.path / "out" / "stable.txt").read_text() == "stable"  # the capture's own run
    assert set(result.exact + result.numeric) == {"out/stable.txt", "out/stable.json"}


def test_cli_record_prints_the_summary_and_the_equivalent_command(lab, capsys):
    code = cli.main(["record", "--name", "exp", "--", sys.executable, "run.py"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "inputs found:   2" in out and "outputs, exact: 1" in out
    assert "Equivalent command" in out and "sciproof capture --name exp" in out
    manifest = json.loads((lab.path / "exp.sciproof.json").read_text())
    assert manifest["command"]["argv"][1:] == ["run.py"]


def test_cli_record_without_a_command_is_an_error(lab, capsys):
    assert cli.main(["record", "--name", "exp"]) == cli.EXIT_ERROR
    assert "no command given" in capsys.readouterr().err
