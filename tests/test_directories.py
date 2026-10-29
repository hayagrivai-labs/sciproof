"""Directories as inputs and exact outputs (manifest schema 1.1, docs/MANIFEST.md)."""

from __future__ import annotations

import json
import os
import sys

import pytest

from sciproof import cli
from sciproof.errors import CaptureError, ManifestError
from sciproof.manifest import load_manifest
from sciproof.treehash import (
    MAX_NAMES_IN_DETAIL,
    TreeDiff,
    TreeError,
    diff_trees,
    scan_tree,
    tree_digest,
    tree_record,
)
from sciproof.verify import Status, report_to_dict, verify

SCRIPT = (
    "import os, pathlib; d = pathlib.Path('results'); d.mkdir(exist_ok=True); "
    "(d / 'a.txt').write_text('alpha'); (d / 'sub').mkdir(exist_ok=True); "
    "(d / 'sub' / 'b.txt').write_text(os.environ.get('SCIPROOF_TEST_SUFFIX', 'beta'))"
)


def _can_symlink(tmp_path) -> bool:
    try:
        os.symlink(tmp_path, tmp_path / "probe-link")
    except (OSError, NotImplementedError):
        return False
    return True


@pytest.fixture
def dirs(project, monkeypatch):
    """A project whose command writes a ``results/`` directory and reads ``inputs/``."""
    monkeypatch.delenv("SCIPROOF_TEST_SUFFIX", raising=False)
    (project.root / "inputs").mkdir()
    # bytes, not text: text mode would write CRLF on Windows and change the sizes
    (project.root / "inputs" / "x.csv").write_bytes(b"1,2\n")
    (project.root / "inputs" / "y.csv").write_bytes(b"3,4\n")

    def capture(**overrides):
        options = {
            "name": "dirs",
            "argv": [sys.executable, "-c", SCRIPT],
            "inputs": ["data.json", "inputs"],
            "outputs": ["results"],
            "numeric_outputs": [],
        }
        options.update(overrides)
        return project.capture(**options)

    project.dirs_capture = capture
    project.dirs_manifest = project.root / "dirs.sciproof.json"
    return project


# ---------------------------------------------------------------- the tree digest


def test_scan_lists_regular_files_sorted_with_posix_paths(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "z.txt").write_text("z")
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b" / "y.txt").write_text("yy")
    entries = scan_tree(tmp_path)
    assert [e.path for e in entries] == ["a.txt", "b/y.txt", "b/z.txt"]
    assert [e.size for e in entries] == [1, 2, 1]


def test_empty_directories_are_not_recorded(tmp_path):
    (tmp_path / "empty").mkdir()
    assert scan_tree(tmp_path) == []


def test_digest_does_not_depend_on_creation_order(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    for root, names in ((first, ["a", "b", "c"]), (second, ["c", "a", "b"])):
        root.mkdir()
        for name in names:
            (root / name).write_text(name)
    assert tree_digest(scan_tree(first)) == tree_digest(scan_tree(second))


def test_digest_changes_with_content_name_and_extra_files(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    (root / "a.txt").write_text("a")
    base = tree_digest(scan_tree(root))
    (root / "a.txt").write_text("b")
    assert tree_digest(scan_tree(root)) != base
    (root / "a.txt").write_text("a")
    (root / "a.txt").rename(root / "b.txt")
    assert tree_digest(scan_tree(root)) != base
    (root / "b.txt").rename(root / "a.txt")
    (root / "extra").write_text("")
    assert tree_digest(scan_tree(root)) != base


def test_same_content_in_two_files_is_not_confused_with_one_file(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "a").write_text("x")
    (two / "a").write_text("x")
    (two / "b").write_text("x")
    assert tree_digest(scan_tree(one)) != tree_digest(scan_tree(two))


def test_the_digest_is_defined_by_the_documented_line_format(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    entry = scan_tree(tmp_path)[0]
    import hashlib

    expected = hashlib.sha256(f"{entry.sha256}  2  a.txt\n".encode()).hexdigest()
    assert tree_digest([entry]) == expected
    assert tree_record([entry])["sha256"] == expected
    assert hashlib.sha256(b"").hexdigest() == tree_digest([])  # an empty tree


def test_too_many_files_are_refused(tmp_path):
    for index in range(3):
        (tmp_path / f"f{index}").write_text("x")
    with pytest.raises(TreeError, match="more than 2 files"):
        scan_tree(tmp_path, limit=2)
    assert len(scan_tree(tmp_path, limit=3)) == 3


def test_unreadable_directory_raises_tree_error(tmp_path, monkeypatch):
    def raise_permission_error(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(os, "scandir", raise_permission_error)

    with pytest.raises(TreeError, match="cannot read"):
        scan_tree(tmp_path)


def test_unreadable_file_raises_tree_error(tmp_path, monkeypatch):
    (tmp_path / "file.txt").write_text("content")

    def raise_os_error(*args, **kwargs):
        raise OSError("disk read error")

    monkeypatch.setattr("sciproof.treehash.sha256_file", raise_os_error)

    with pytest.raises(TreeError, match="cannot read"):
        scan_tree(tmp_path)


def test_non_regular_files_and_directories_raise_tree_error(tmp_path, monkeypatch):
    class SpecialEntry:
        name = "special"

        def is_symlink(self):
            return False

        def is_dir(self, *, follow_symlinks=True):
            return False

        def is_file(self, *, follow_symlinks=True):
            return False

    class FakeScandir:
        def __enter__(self):
            return iter([SpecialEntry()])

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(os, "scandir", lambda *args, **kwargs: FakeScandir())

    with pytest.raises(TreeError, match="not a regular file or directory"):
        scan_tree(tmp_path)


def test_symbolic_links_inside_a_tree_are_refused(tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("symbolic links are not available here")
    root = tmp_path / "t"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("secret")
    os.symlink(tmp_path / "outside.txt", root / "link.txt")
    with pytest.raises(TreeError, match="symbolic link"):
        scan_tree(root)


def test_a_symbolic_link_to_a_directory_is_not_followed(tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("symbolic links are not available here")
    root = tmp_path / "t"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "f").write_text("x")
    os.symlink(tmp_path / "elsewhere", root / "dirlink", target_is_directory=True)
    with pytest.raises(TreeError, match="symbolic link"):
        scan_tree(root)


# ---------------------------------------------------------------- comparing two trees


def test_diff_trees_reports_added_removed_and_modified(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    for name in ("keep", "change", "gone"):
        (root / name).write_text(name)
    recorded = tree_record(scan_tree(root))["files"]
    (root / "change").write_text("different")
    (root / "gone").unlink()
    (root / "new").write_text("n")
    diff = diff_trees(recorded, scan_tree(root))
    assert (diff.added, diff.removed, diff.modified) == (["new"], ["gone"], ["change"])
    assert diff.describe() == "1 modified (change); 1 removed (gone); 1 added (new)"
    assert not diff.empty


def test_diff_descriptions_and_json_are_bounded():
    many = [f"f{i:03}" for i in range(25)]
    diff = TreeDiff(added=many, removed=[], modified=[])
    assert diff.describe(shown=3) == "25 added (f000, f001, f002, and 22 more)"
    data = diff.to_dict()
    assert len(data["added"]) == MAX_NAMES_IN_DETAIL
    assert data["added_count"] == 25


# ---------------------------------------------------------------- capture


def test_capture_records_directories_with_a_file_list(dirs):
    result = dirs.dirs_capture()
    manifest = result.manifest
    assert manifest["schema_version"] == "1.1"
    folder = next(i for i in manifest["inputs"] if i["path"] == "inputs")
    assert folder["kind"] == "directory"
    assert [f["path"] for f in folder["files"]] == ["x.csv", "y.csv"]
    assert folder["size"] == 8
    out = manifest["outputs"][0]
    assert out["kind"] == "directory"
    assert out["present"] is True
    assert [f["path"] for f in out["files"]] == ["a.txt", "sub/b.txt"]
    assert out["compare"] == {"mode": "exact"}
    load_manifest(dirs.dirs_manifest)  # and it validates


def test_a_manifest_without_directories_stays_at_schema_1_0(project):
    assert project.capture().manifest["schema_version"] == "1.0"


def test_numeric_comparison_of_a_directory_is_refused(dirs):
    (dirs.root / "results").mkdir()
    with pytest.raises(CaptureError, match="--output-numeric compares single"):
        dirs.dirs_capture(outputs=[], numeric_outputs=["results"])


def test_a_directory_that_the_command_never_creates_is_an_error(dirs):
    with pytest.raises(CaptureError, match="does not exist"):
        dirs.dirs_capture(argv=[sys.executable, "-c", "pass"])


def test_the_command_must_not_change_an_input_directory(dirs):
    code = "import pathlib; pathlib.Path('inputs/new.csv').write_text('5')"
    with pytest.raises(CaptureError, match="modified or removed the declared input 'inputs'"):
        dirs.dirs_capture(argv=[sys.executable, "-c", code], outputs=[])


def test_a_file_inside_a_declared_directory_is_refused(dirs):
    with pytest.raises(CaptureError, match="lies inside the declared"):
        dirs.dirs_capture(inputs=["data.json", "inputs", "inputs/x.csv"])


def test_an_output_inside_an_input_directory_is_refused(dirs):
    with pytest.raises(CaptureError, match="lies inside the declared"):
        dirs.dirs_capture(outputs=["inputs/out.txt"], argv=[sys.executable, "-c", "pass"])


def test_the_evidence_files_cannot_be_declared(dirs):
    with pytest.raises(CaptureError, match="where the evidence of this capture is written"):
        dirs.dirs_capture(outputs=["dirs.sciproof.d"])
    with pytest.raises(CaptureError, match="where the evidence of this capture is written"):
        dirs.dirs_capture(inputs=["data.json", "dirs.sciproof.json"])


def test_symlinks_in_a_declared_input_directory_are_refused(dirs):
    if not _can_symlink(dirs.root):
        pytest.skip("symbolic links are not available here")
    os.symlink(dirs.root / "data.json", dirs.root / "inputs" / "link.json")
    with pytest.raises(CaptureError, match="symbolic link"):
        dirs.dirs_capture()
    assert not dirs.dirs_manifest.exists()


# ---------------------------------------------------------------- verify (integrity)


def test_unchanged_directories_pass(dirs):
    dirs.dirs_capture()
    report = verify(dirs.dirs_manifest)
    assert report.status is Status.PASS
    messages = {c.subject: c.message for c in report.checks}
    assert messages["input inputs"] == "unchanged (2 files, tree SHA-256 matches)"
    assert messages["output results"] == "unchanged (2 files, tree SHA-256 matches)"


@pytest.mark.parametrize("target", ["inputs/x.csv", "results/sub/b.txt"])
def test_a_modified_file_inside_a_directory_fails_and_is_named(dirs, target):
    dirs.dirs_capture()
    (dirs.root / target).write_text("tampered")
    report = verify(dirs.dirs_manifest)
    assert report.status is Status.FAIL
    failed = next(c for c in report.checks if c.status is Status.FAIL)
    assert "1 modified" in failed.message
    assert target.split("/", 1)[1] in failed.message
    assert failed.evidence["artifact"] == "directory"
    assert failed.evidence["tree"]["modified"] == [target.split("/", 1)[1]]


def test_an_added_file_fails(dirs):
    dirs.dirs_capture()
    (dirs.root / "results" / "extra.txt").write_text("new")
    report = verify(dirs.dirs_manifest)
    assert report.status is Status.FAIL
    check = next(c for c in report.checks if c.status is Status.FAIL)
    assert "1 added (extra.txt)" in check.message
    assert check.evidence["files_expected"] == 2
    assert check.evidence["files_actual"] == 3


def test_a_removed_file_fails(dirs):
    dirs.dirs_capture()
    (dirs.root / "results" / "a.txt").unlink()
    report = verify(dirs.dirs_manifest)
    assert report.status is Status.FAIL
    assert "1 removed (a.txt)" in next(c for c in report.checks if c.status is Status.FAIL).message


def test_a_missing_input_directory_is_inconclusive_a_missing_output_fails(dirs):
    import shutil

    dirs.dirs_capture()
    shutil.rmtree(dirs.root / "inputs")
    assert verify(dirs.dirs_manifest).status is Status.INCONCLUSIVE
    shutil.rmtree(dirs.root / "results")
    report = verify(dirs.dirs_manifest)
    assert report.status is Status.FAIL
    assert "missing" in next(c for c in report.checks if c.subject == "output results").message


def test_a_symlink_added_later_fails_instead_of_being_followed(dirs):
    if not _can_symlink(dirs.root):
        pytest.skip("symbolic links are not available here")
    dirs.dirs_capture()
    os.symlink(dirs.root / "data.json", dirs.root / "results" / "link.json")
    report = verify(dirs.dirs_manifest)
    assert report.status is Status.FAIL
    assert "symbolic link" in next(c for c in report.checks if c.status is Status.FAIL).message


def test_a_directory_reported_in_json_carries_the_tree_evidence(dirs):
    dirs.dirs_capture()
    (dirs.root / "results" / "a.txt").write_text("changed")
    data = report_to_dict(verify(dirs.dirs_manifest))
    evidence = next(c for c in data["checks"] if c["subject"] == "output results")["evidence"]
    assert evidence["kind"] == "output_integrity"
    assert evidence["tree"]["modified_count"] == 1
    json.dumps(data)


# ---------------------------------------------------------------- verify --rerun


def test_rerun_of_a_deterministic_directory_output_passes_and_leaves_the_tree(dirs):
    dirs.dirs_capture()
    before = tree_digest(scan_tree(dirs.root / "results"))
    report = verify(dirs.dirs_manifest, rerun=True, assume_yes=True)
    assert report.status is Status.PASS, report.checks
    assert tree_digest(scan_tree(dirs.root / "results")) == before
    assert report.rerun_outputs_dir is None  # identical result: nothing moved aside


def test_rerun_with_a_different_directory_fails_and_restores_the_original(dirs, monkeypatch):
    dirs.dirs_capture()
    original = tree_digest(scan_tree(dirs.root / "results"))
    monkeypatch.setenv("SCIPROOF_TEST_SUFFIX", "changed")
    report = verify(dirs.dirs_manifest, rerun=True, assume_yes=True)
    assert report.status is Status.FAIL
    check = next(c for c in report.checks if c.subject == "rerun output results")
    assert "1 modified (sub/b.txt)" in check.message
    assert tree_digest(scan_tree(dirs.root / "results")) == original  # evidence is untouched
    moved = dirs.root / report.rerun_outputs_dir / "results" / "sub" / "b.txt"
    assert moved.read_text() == "changed"
    assert verify(dirs.dirs_manifest).status is Status.PASS


def test_rerun_that_adds_a_file_to_the_directory_is_detected_and_undone(dirs, monkeypatch):
    dirs.dirs_capture()
    sneaky = (
        SCRIPT + "; (d / 'extra.txt').write_text('x') if os.environ.get('SCIPROOF_EXTRA') else 0"
    )
    manifest = json.loads(dirs.dirs_manifest.read_text())
    manifest["command"]["argv"] = [sys.executable, "-c", sneaky]
    dirs.dirs_manifest.write_text(json.dumps(manifest))
    monkeypatch.setenv("SCIPROOF_EXTRA", "1")
    report = verify(dirs.dirs_manifest, rerun=True, assume_yes=True)
    assert report.status is Status.FAIL
    check = next(c for c in report.checks if c.subject == "rerun output results")
    assert "1 added (extra.txt)" in check.message
    assert not (dirs.root / "results" / "extra.txt").exists()
    assert verify(dirs.dirs_manifest).status is Status.PASS


def test_rerun_recreates_a_missing_output_directory_and_removes_it_again(dirs):
    import shutil

    dirs.dirs_capture()
    shutil.rmtree(dirs.root / "results")
    report = verify(dirs.dirs_manifest, rerun=True, assume_yes=True)
    check = next(c for c in report.checks if c.subject == "rerun output results")
    assert check.status is Status.PASS  # the command made the recorded directory again
    assert not (dirs.root / "results").exists()  # and the tree is left as it was found
    assert report.rerun_outputs_dir is not None


def test_a_rerun_that_changes_an_input_directory_is_reported(dirs):
    dirs.dirs_capture()
    manifest = json.loads(dirs.dirs_manifest.read_text())
    code = "import pathlib; pathlib.Path('inputs/x.csv').write_text('changed')"
    manifest["command"]["argv"] = [sys.executable, "-c", SCRIPT + "; " + code]
    dirs.dirs_manifest.write_text(json.dumps(manifest))
    report = verify(dirs.dirs_manifest, rerun=True, assume_yes=True)
    assert report.status is Status.FAIL
    assert any("modified or removed by the rerun" in c.message for c in report.checks)


# ---------------------------------------------------------------- manifest validation


def _mutated(dirs, mutate):
    dirs.dirs_capture()
    data = json.loads(dirs.dirs_manifest.read_text())
    mutate(data)
    dirs.dirs_manifest.write_text(json.dumps(data))
    return dirs.dirs_manifest


def _input(data):
    return next(i for i in data["inputs"] if i["path"] == "inputs")


def test_a_directory_entry_in_a_1_0_manifest_is_refused(dirs):
    path = _mutated(dirs, lambda d: d.update(schema_version="1.0"))
    with pytest.raises(ManifestError, match="need schema_version 1.1"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: _input(d).update(sha256="0" * 64), "does not match the digest"),
        (lambda d: _input(d).update(size=999), "does not match the sizes"),
        (lambda d: _input(d)["files"].append(_input(d)["files"][0]), "duplicate path"),
        (lambda d: _input(d)["files"][0].update(path="../escape"), "'..'"),
        (lambda d: _input(d)["files"][0].update(path="/etc/passwd"), "absolute"),
        (lambda d: _input(d)["files"][0].update(sha256="xyz"), "SHA-256"),
        (lambda d: _input(d).update(files="nope"), "expected a list"),
        (lambda d: _input(d).update(kind="symlink"), "expected 'file' or 'directory'"),
        (lambda d: d["inputs"][0].update(files=[]), "only a directory has a list"),
    ],
)
def test_inconsistent_or_unsafe_directory_entries_are_refused(dirs, mutate, message):
    path = _mutated(dirs, mutate)
    with pytest.raises(ManifestError, match=message):
        load_manifest(path)


def test_a_declared_file_inside_a_declared_directory_is_refused_in_a_manifest(dirs):
    def add_nested(data):
        data["inputs"].append({"path": "inputs/x.csv", "sha256": "0" * 64, "size": 1})

    with pytest.raises(ManifestError, match="lies inside the declared directory"):
        load_manifest(_mutated(dirs, add_nested))


def test_a_numeric_directory_output_is_refused_in_a_manifest(dirs):
    def make_numeric(data):
        data["outputs"][0]["compare"] = {
            "mode": "numeric",
            "rtol": 0.0,
            "atol": 0.0,
            "snapshot": {"path": "dirs.sciproof.d/0-x.json", "sha256": "0" * 64},
        }

    with pytest.raises(ManifestError, match="only be compared exactly"):
        load_manifest(_mutated(dirs, make_numeric))


def test_too_many_listed_files_are_refused(dirs, monkeypatch):
    path = _mutated(dirs, lambda d: None)
    monkeypatch.setattr("sciproof.manifest.MAX_TREE_FILES", 1)
    with pytest.raises(ManifestError, match="more than 1 files"):
        load_manifest(path)


# ---------------------------------------------------------------- command line


def test_cli_roundtrip_with_directories(dirs, capsys):
    code = [
        "capture",
        "--name",
        "cli",
        "--input",
        "inputs",
        "--output",
        "results",
        "--",
        sys.executable,
        "-c",
        SCRIPT,
    ]
    assert cli.main(code) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["inspect", "cli.sciproof.json"]) == cli.EXIT_OK
    inspected = capsys.readouterr().out
    assert "inputs  directory, 2 files, tree sha256:" in inspected
    assert "results  directory, 2 files, tree sha256:" in inspected
    assert cli.main(["verify", "cli.sciproof.json"]) == cli.EXIT_OK
    capsys.readouterr()
    (dirs.root / "results" / "a.txt").write_text("tampered")
    assert cli.main(["verify", "cli.sciproof.json"]) == cli.EXIT_FAIL
    assert "1 modified (a.txt)" in capsys.readouterr().out


# ---------------------------------------------------------------- names that are not portable


class _FakeEntry:
    """A minimal ``os.DirEntry`` so that names Windows cannot create can be tested everywhere."""

    def __init__(self, directory, name):
        self.name = name
        self.path = str(directory / name)

    def is_symlink(self):
        return False

    def is_dir(self, follow_symlinks=True):
        return False

    def is_file(self, follow_symlinks=True):
        return True


class _FakeScandir:
    def __init__(self, directory, names):
        self._entries = [_FakeEntry(directory, name) for name in names]

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, *exc_info):
        return False


def _scan_names(tmp_path, monkeypatch, names):
    monkeypatch.setattr("sciproof.treehash.os.scandir", lambda d: _FakeScandir(tmp_path, names))
    monkeypatch.setattr("sciproof.treehash.sha256_file", lambda path: ("0" * 64, 1))
    return scan_tree(tmp_path)


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("aux.txt", "reserved Windows device name"),
        ("NUL", "reserved Windows device name"),
        ("com1.log", "reserved Windows device name"),
        ("trail.", "ending with"),
        ("trail ", "ending with"),
        ("two\nlines", "control character"),
        (r"back\slash", "backslash"),
    ],
)
def test_names_that_mean_something_else_elsewhere_are_refused(tmp_path, monkeypatch, name, message):
    """These would make the manifest invalid at write time, so the scan refuses them first."""
    with pytest.raises(TreeError, match=message):
        _scan_names(tmp_path, monkeypatch, ["ok.txt", name])


def test_ordinary_names_close_to_reserved_ones_are_accepted(tmp_path, monkeypatch):
    names = ["auxiliary.txt", "console", "com10.txt", "lpt0", ".hidden", "a b.txt", "ünï.txt"]
    entries = _scan_names(tmp_path, monkeypatch, names)
    assert sorted(e.path for e in entries) == sorted(names)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot create these names")
def test_capture_reports_a_non_portable_file_name_instead_of_failing_late(dirs):
    code = (
        "import pathlib; d = pathlib.Path('results'); d.mkdir(exist_ok=True); "
        "(d / 'aux.txt').write_text('x')"
    )
    with pytest.raises(CaptureError, match="cannot record 'aux.txt'.*reserved Windows device"):
        dirs.dirs_capture(argv=[sys.executable, "-c", code])
    assert not dirs.dirs_manifest.exists()
