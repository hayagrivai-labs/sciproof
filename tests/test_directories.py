from __future__ import annotations

import copy
import json
import shutil
import sys

import pytest

from sciproof.capture import capture
from sciproof.errors import CaptureError, ManifestError
from sciproof.inspect_manifest import inspect_file
from sciproof.manifest import load_manifest, validate_manifest
from sciproof.tree import diff_tree, list_tree, tree_digest
from sciproof.verify import Status, verify

# Reads the whole "dataset" directory and writes results/a.txt and results/sub/b.txt.
WRITE_RESULTS = (
    "import pathlib; "
    "total = sum(len(p.read_bytes()) for p in pathlib.Path('dataset').rglob('*') if p.is_file()); "
    "pathlib.Path('results/sub').mkdir(parents=True, exist_ok=True); "
    "pathlib.Path('results/a.txt').write_text('total=' + str(total)); "
    "pathlib.Path('results/sub/b.txt').write_text('b'); "
    "open('marker.txt', 'a').write('ran' + chr(10))"
)


@pytest.fixture
def dirs(project):
    dataset = project.root / "dataset"
    (dataset / "nested").mkdir(parents=True)
    (dataset / "one.csv").write_text("1,2")
    (dataset / "nested" / "two.csv").write_text("3,4")

    def run(**overrides):
        options = {
            "argv": [sys.executable, "-c", WRITE_RESULTS],
            "inputs": ["dataset"],
            "outputs": ["results"],
            "numeric_outputs": [],
        }
        options.update(overrides)
        return project.capture(**options)

    project.run = run
    return project


def test_directories_are_recorded_with_their_files(dirs):
    result = dirs.run()
    manifest = load_manifest(dirs.manifest)
    assert manifest["schema_version"] == "1.1"
    (dataset,) = manifest["inputs"]
    assert dataset["kind"] == "directory"
    assert [f["path"] for f in dataset["files"]] == ["nested/two.csv", "one.csv"]
    assert dataset["sha256"] == tree_digest(dataset["files"])
    assert dataset["size"] == 6
    (results,) = manifest["outputs"]
    assert results["kind"] == "directory" and results["present"] is True
    assert {f["path"] for f in results["files"]} == {"a.txt", "sub/b.txt"}
    assert result.exit_code == 0


def test_manifests_without_directories_keep_version_1_0(project):
    project.capture()
    assert load_manifest(project.manifest)["schema_version"] == "1.0"


def test_unchanged_directories_verify(dirs):
    dirs.run()
    report = verify(dirs.manifest)
    assert report.status is Status.PASS
    assert any("all 2 files match" in c.message for c in report.checks)


def test_modified_added_and_removed_files_are_detected(dirs):
    dirs.run()
    dataset = dirs.root / "dataset"

    (dataset / "one.csv").write_text("changed")
    report = verify(dirs.manifest)
    assert report.status is Status.FAIL
    assert any("modified: one.csv" in c.message for c in report.checks)
    (dataset / "one.csv").write_text("1,2")

    (dataset / "extra.csv").write_text("new")
    assert any("added: extra.csv" in c.message for c in verify(dirs.manifest).checks)
    (dataset / "extra.csv").unlink()

    (dataset / "nested" / "two.csv").unlink()
    assert any("removed: nested/two.csv" in c.message for c in verify(dirs.manifest).checks)


def test_modified_output_directory_is_detected(dirs):
    dirs.run()
    (dirs.root / "results" / "sub" / "b.txt").write_text("tampered")
    report = verify(dirs.manifest)
    assert report.status is Status.FAIL
    assert any("modified: sub/b.txt" in c.message for c in report.checks)


def test_missing_input_directory_is_inconclusive(dirs):
    dirs.run()
    shutil.rmtree(dirs.root / "dataset")
    assert verify(dirs.manifest).status is Status.INCONCLUSIVE


def test_rerun_of_a_directory_output_passes_and_keeps_the_original(dirs):
    dirs.run()
    original = (dirs.root / "results" / "a.txt").read_text()
    report = verify(dirs.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.PASS
    assert dirs.runs() == 2
    assert (dirs.root / "results" / "a.txt").read_text() == original
    assert (dirs.root / report.rerun_outputs_dir / "results" / "sub" / "b.txt").is_file()


def test_rerun_detects_a_different_directory_output(dirs):
    dirs.run()
    manifest = json.loads(dirs.manifest.read_text())
    # Pretend the experiment used to write different content.
    manifest["outputs"][0]["files"][0]["sha256"] = "0" * 64
    manifest["outputs"][0]["sha256"] = tree_digest(manifest["outputs"][0]["files"])
    dirs.manifest.write_text(json.dumps(manifest))
    report = verify(dirs.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.FAIL
    assert any("differs from the recorded directory" in c.message for c in report.checks)


def test_the_command_must_not_change_an_input_directory(dirs):
    code = "import pathlib; pathlib.Path('dataset/new.csv').write_text('x')"
    with pytest.raises(CaptureError, match="modified or removed the declared input"):
        dirs.run(argv=[sys.executable, "-c", code], outputs=[])


def test_numeric_comparison_of_a_directory_is_rejected(dirs):
    with pytest.raises(CaptureError, match="--output-numeric needs"):
        dirs.run(outputs=[], numeric_outputs=["dataset"], inputs=["data.json"])


def test_an_empty_directory_has_an_empty_listing(dirs):
    (dirs.root / "empty").mkdir()
    dirs.run(inputs=["empty"])
    (entry,) = load_manifest(dirs.manifest)["inputs"]
    assert entry["files"] == [] and entry["size"] == 0


def test_a_symlink_inside_a_directory_is_refused(dirs):
    link = dirs.root / "dataset" / "link"
    try:
        link.symlink_to(dirs.root / "data.json")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    with pytest.raises(CaptureError, match="not a regular file"):
        dirs.run()


def test_inspect_shows_directories(dirs):
    dirs.run()
    text = inspect_file(dirs.manifest)
    assert "directory, 2 files, 6 bytes" in text
    assert "directory, 2 files" in text.split("Outputs")[1]


def test_tree_digest_does_not_depend_on_listing_order(dirs):
    files = list_tree(dirs.root / "dataset")
    assert tree_digest(files) == tree_digest(list(reversed(files)))


def test_diff_tree_is_limited():
    recorded = [{"path": f"f{i}", "sha256": "a" * 64, "size": 1} for i in range(30)]
    differences = diff_tree(recorded, [])
    assert len(differences) == 11 and differences[-1] == "... and 20 more"


@pytest.fixture
def good(dirs):
    dirs.run()
    return json.loads(dirs.manifest.read_text())


def _mutate(good, change):
    data = copy.deepcopy(good)
    change(data)
    return data


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["inputs"][0].update(sha256="b" * 64),  # no longer matches the listing
        lambda m: m["inputs"][0].update(size=999),
        lambda m: m["inputs"][0]["files"][0].update(path="../escape.txt"),
        lambda m: m["inputs"][0]["files"][0].update(path="/abs.txt"),
        lambda m: m["inputs"][0]["files"].append(dict(m["inputs"][0]["files"][0])),
        lambda m: m["inputs"][0]["files"][0].update(size=-1),
        lambda m: m["inputs"][0]["files"][0].update(sha256="xyz"),
        lambda m: m["inputs"][0].update(files="not a list"),
        lambda m: m["inputs"][0].update(kind="socket"),
        lambda m: m["inputs"][0].pop("files"),
    ],
    ids=[
        "digest",
        "size",
        "traversal",
        "absolute",
        "duplicate",
        "negative size",
        "bad hash",
        "files not a list",
        "unknown kind",
        "missing files",
    ],
)
def test_invalid_directory_entries_are_rejected(good, change):
    with pytest.raises(ManifestError):
        validate_manifest(_mutate(good, change))


def test_a_file_entry_must_not_carry_a_file_list(good):
    def change(manifest):
        manifest["inputs"][0].update(kind="file")

    with pytest.raises(ManifestError, match="only allowed for directories"):
        validate_manifest(_mutate(good, change))


def test_capture_function_accepts_directories_without_the_project_fixture(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.txt").write_text("x")
    result = capture(
        name="plain",
        argv=[sys.executable, "-c", "pass"],
        base=tmp_path,
        inputs=["d"],
    )
    assert result.manifest["inputs"][0]["kind"] == "directory"
