from __future__ import annotations

import copy
import json

import pytest

from sciproof.errors import ManifestError, UnsafePathError
from sciproof.manifest import MAX_MANIFEST_BYTES, load_manifest, validate_manifest
from sciproof.paths import resolve_inside, to_manifest_path, validate_relative_path


@pytest.fixture
def good(project):
    project.capture()
    return json.loads(project.manifest.read_text())


def test_a_captured_manifest_is_valid(good):
    assert validate_manifest(good) is good


def test_unknown_minor_version_is_accepted_but_unknown_major_is_not(good):
    newer = copy.deepcopy(good)
    newer["schema_version"] = "1.7"
    validate_manifest(newer)
    future = copy.deepcopy(good)
    future["schema_version"] = "2.0"
    with pytest.raises(ManifestError, match="unsupported manifest version"):
        validate_manifest(future)


def _set(path, value):
    def mutate(data):
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _delete(path):
    def mutate(data):
        target = data
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]

    return mutate


BAD_MUTATIONS = {
    "wrong schema": _set(["schema"], "something.else"),
    "negative input size": _set(["inputs", 0, "size"], -5),
    "negative output size": _set(["outputs", 0, "size"], -1),
    "negative log size": _set(["execution", "stdout", "size"], -1),
    "bad version string": _set(["schema_version"], "one"),
    "missing name": _delete(["name"]),
    "invalid name": _set(["name"], "../evil"),
    "argv not a list": _set(["command", "argv"], "python run.py"),
    "argv empty": _set(["command", "argv"], []),
    "argv item not a string": _set(["command", "argv"], ["python", 3]),
    "empty executable": _set(["command", "argv"], [""]),
    "cwd elsewhere": _set(["command", "cwd"], "/etc"),
    "missing environment": _delete(["environment"]),
    "dependencies not a list": _set(["dependencies"], {}),
    "git dirty not a bool": _set(["git"], {"commit": "abc", "dirty": "no"}),
    "input hash too short": _set(["inputs", 0, "sha256"], "abc"),
    "input hash uppercase": _set(["inputs", 0, "sha256"], "A" * 64),
    "input size is a boolean": _set(["inputs", 0, "size"], True),
    "absolute input path": _set(["inputs", 0, "path"], "/etc/passwd"),
    "windows absolute path": _set(["inputs", 0, "path"], "C:/Windows/system.ini"),
    "windows drive relative": _set(["inputs", 0, "path"], "C:evil.txt"),
    "traversal in input": _set(["inputs", 0, "path"], "../secret.txt"),
    "traversal in the middle": _set(["inputs", 0, "path"], "a/../../secret.txt"),
    "backslash path": _set(["inputs", 0, "path"], "a\\b.txt"),
    "nul in path": _set(["inputs", 0, "path"], "a\x00b"),
    "empty path": _set(["inputs", 0, "path"], ""),
    "traversal in output": _set(["outputs", 0, "path"], "../../outside.txt"),
    "exit code is not an int": _set(["execution", "exit_code"], "0"),
    "negative duration": _set(["execution", "duration_seconds"], -1),
    "stdout missing hash": _delete(["execution", "stdout", "sha256"]),
    "unknown compare mode": _set(["outputs", 0, "compare", "mode"], "fuzzy"),
    "numeric without tolerance": _delete(["outputs", 1, "compare", "rtol"]),
    "negative tolerance": _set(["outputs", 1, "compare", "atol"], -1),
    "nan tolerance": _set(["outputs", 1, "compare", "rtol"], float("nan")),
    "snapshot traversal": _set(["outputs", 1, "compare", "snapshot", "path"], "../x.json"),
    "absent output with a hash": _set(["outputs", 0, "present"], False),
}


@pytest.mark.parametrize("mutation", BAD_MUTATIONS.values(), ids=BAD_MUTATIONS.keys())
def test_invalid_manifests_are_rejected(good, mutation):
    data = copy.deepcopy(good)
    # outputs are ordered: exact first, numeric second (see Project.capture)
    mutation(data)
    with pytest.raises(ManifestError):
        validate_manifest(data)


def test_duplicate_paths_are_rejected(good):
    data = copy.deepcopy(good)
    data["inputs"].append(copy.deepcopy(data["inputs"][0]))
    with pytest.raises(ManifestError, match="duplicate"):
        validate_manifest(data)


@pytest.mark.parametrize(
    "content", ["", "not json", "[]", "null", '{"schema": "sciproof.manifest"}']
)
def test_garbage_files_are_rejected(project, content):
    project.manifest.write_text(content)
    with pytest.raises(ManifestError):
        load_manifest(project.manifest)


def test_non_utf8_manifest_is_rejected(project):
    project.manifest.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ManifestError, match="UTF-8"):
        load_manifest(project.manifest)


def test_deeply_nested_json_is_rejected_cleanly(project):
    project.manifest.write_text("[" * 200000 + "]" * 200000)
    with pytest.raises(ManifestError):
        load_manifest(project.manifest)


def test_missing_manifest_and_directories(project):
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(project.root / "missing.sciproof.json")
    with pytest.raises(ManifestError, match="not a regular file"):
        load_manifest(project.root)


def test_oversized_manifest_is_rejected(project, monkeypatch):
    project.manifest.write_text("{}")
    monkeypatch.setattr("sciproof.manifest.MAX_MANIFEST_BYTES", 1)
    assert MAX_MANIFEST_BYTES > 1
    with pytest.raises(ManifestError, match="larger than"):
        load_manifest(project.manifest)


# ------------------------------------------------------------------------------- paths


@pytest.mark.parametrize(
    "value",
    ["/abs", "C:/x", "C:x", "../x", "a/../b", "a//b", "a/./b", ".", "", "a\\b", "x\x00y", None, 5],
)
def test_validate_relative_path_rejects_unsafe_values(value):
    with pytest.raises(UnsafePathError):
        validate_relative_path(value)


@pytest.mark.parametrize("value", ["a", "a/b.txt", "dir with space/f.csv", ".hidden/file"])
def test_validate_relative_path_accepts_normal_values(value):
    assert validate_relative_path(value) == value


def test_resolve_inside_rejects_symlink_escape(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    try:
        (base / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    with pytest.raises(UnsafePathError):
        resolve_inside(base, "link/secret.txt")


def test_to_manifest_path_rejects_paths_outside_the_base(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "other.txt").write_text("x")
    with pytest.raises(UnsafePathError):
        to_manifest_path(base, "../other.txt")
    with pytest.raises(UnsafePathError):
        to_manifest_path(base, tmp_path / "other.txt")
    assert to_manifest_path(base, base / "sub" / "f.txt") == "sub/f.txt"
