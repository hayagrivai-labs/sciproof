"""The versioned JSON evidence manifest: reading, strict validation and writing.

A manifest is treated as untrusted input. Validation checks types, versions, hashes and
paths, but a *valid* manifest still contains a command that can run arbitrary code, which is
why SciProof never executes it unless the user explicitly asks to.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from sciproof.errors import ManifestError, SciProofError, UnsafePathError
from sciproof.paths import validate_relative_path
from sciproof.tree import tree_digest, validate_tree_files

SCHEMA = "sciproof.manifest"
SCHEMA_VERSION = "1.0"
# Manifests that describe directories use 1.1 (older readers treat them as unsupported).
SCHEMA_VERSION_DIRECTORIES = "1.1"
SUPPORTED_MAJOR = 1
MANIFEST_SUFFIX = ".sciproof.json"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


def snapshot_dir_name(name: str) -> str:
    """Directory (next to the manifest) that stores copies of numeric outputs."""
    return f"{name}.sciproof.d"


def _fail(where: str, message: str) -> ManifestError:
    return ManifestError(f"invalid manifest: {where}: {message}")


def _get(obj: dict[str, Any], key: str, types: type | tuple[type, ...], where: str) -> Any:
    if key not in obj:
        raise _fail(where, f"missing required field {key!r}")
    value = obj[key]
    if isinstance(value, bool) and bool not in (types if isinstance(types, tuple) else (types,)):
        raise _fail(f"{where}.{key}", "unexpected boolean")
    if not isinstance(value, types):
        expected = (
            types.__name__ if isinstance(types, type) else "/".join(t.__name__ for t in types)
        )
        raise _fail(f"{where}.{key}", f"expected {expected}, got {type(value).__name__}")
    return value


def _as_dict(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(where, f"expected an object, got {type(value).__name__}")
    return value


def _sha(value: Any, where: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise _fail(where, "expected a lowercase 64-character hex SHA-256 digest")
    return value


def _rel_path(value: Any, where: str) -> str:
    try:
        return validate_relative_path(value)
    except UnsafePathError as exc:
        raise _fail(where, str(exc)) from exc


def _finite_nonneg(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(where, "expected a number")
    if not math.isfinite(value) or value < 0:
        raise _fail(where, "expected a finite number >= 0")
    return float(value)


def _check_kind(item: dict[str, Any], where: str) -> None:
    """Validate the optional ``kind`` of an input or output (``file`` is the default)."""
    kind = item.get("kind", "file")
    if kind not in ("file", "directory"):
        raise _fail(f"{where}.kind", f"expected 'file' or 'directory', got {kind!r}")
    if kind == "file":
        if "files" in item:
            raise _fail(f"{where}.files", "only allowed for directories")
        return
    try:
        files = validate_tree_files(item.get("files"), f"{where}.files")
    except SciProofError as exc:
        raise _fail(f"{where}.files", str(exc)) from exc
    except ValueError as exc:
        raise _fail(where, str(exc)) from exc
    if item["sha256"] != tree_digest(files):
        raise _fail(f"{where}.sha256", "does not match the listed files")
    if item["size"] != sum(entry["size"] for entry in files):
        raise _fail(f"{where}.size", "does not match the listed files")


def _validate_stream(stream: Any, where: str) -> None:
    stream = _as_dict(stream, where)
    _sha(stream.get("sha256"), f"{where}.sha256")
    _get(stream, "size", int, where)
    _get(stream, "truncated", bool, where)
    _get(stream, "text", str, where)


def validate_manifest(data: Any) -> dict[str, Any]:
    """Validate the structure of a parsed manifest and return it. Raises ``ManifestError``."""
    manifest = _as_dict(data, "manifest")

    if manifest.get("schema") != SCHEMA:
        raise _fail("schema", f"expected {SCHEMA!r}, got {manifest.get('schema')!r}")
    version = _get(manifest, "schema_version", str, "manifest")
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise _fail("schema_version", f"cannot parse version {version!r}")
    if int(match.group(1)) != SUPPORTED_MAJOR:
        raise ManifestError(
            f"unsupported manifest version {version!r}; this SciProof reads major version "
            f"{SUPPORTED_MAJOR}.x"
        )

    name = _get(manifest, "name", str, "manifest")
    if not NAME_RE.fullmatch(name):
        raise _fail("name", f"{name!r} is not a valid experiment name")
    _get(manifest, "created_at", str, "manifest")

    command = _as_dict(_get(manifest, "command", dict, "manifest"), "command")
    argv = _get(command, "argv", list, "command")
    if not argv or not all(isinstance(item, str) and "\x00" not in item for item in argv):
        raise _fail("command.argv", "expected a non-empty list of strings")
    if not argv[0]:
        raise _fail("command.argv[0]", "executable must not be empty")
    if _get(command, "cwd", str, "command") != ".":
        raise _fail("command.cwd", "only '.' (the manifest directory) is supported")

    environment = _as_dict(_get(manifest, "environment", dict, "manifest"), "environment")
    python = _as_dict(_get(environment, "python", dict, "environment"), "environment.python")
    _get(python, "version", str, "environment.python")
    _get(environment, "platform", dict, "environment")
    _get(environment, "env_vars", dict, "environment")

    dependencies = _get(manifest, "dependencies", list, "manifest")
    for index, dep in enumerate(dependencies):
        dep = _as_dict(dep, f"dependencies[{index}]")
        _get(dep, "name", str, f"dependencies[{index}]")
        _get(dep, "version", str, f"dependencies[{index}]")

    git = manifest.get("git", None)
    if git is not None:
        git = _as_dict(git, "git")
        _get(git, "commit", str, "git")
        _get(git, "dirty", bool, "git")

    seen: set[str] = set()
    for index, item in enumerate(_get(manifest, "inputs", list, "manifest")):
        where = f"inputs[{index}]"
        item = _as_dict(item, where)
        path = _rel_path(item.get("path"), f"{where}.path")
        if path in seen:
            raise _fail(f"{where}.path", f"duplicate path {path!r}")
        seen.add(path)
        _sha(item.get("sha256"), f"{where}.sha256")
        _get(item, "size", int, where)
        _check_kind(item, where)

    execution = _as_dict(_get(manifest, "execution", dict, "manifest"), "execution")
    _get(execution, "exit_code", int, "execution")
    _finite_nonneg(execution.get("duration_seconds"), "execution.duration_seconds")
    _validate_stream(execution.get("stdout"), "execution.stdout")
    _validate_stream(execution.get("stderr"), "execution.stderr")

    seen_outputs: set[str] = set()
    for index, item in enumerate(_get(manifest, "outputs", list, "manifest")):
        where = f"outputs[{index}]"
        item = _as_dict(item, where)
        path = _rel_path(item.get("path"), f"{where}.path")
        if path in seen_outputs or path in seen:
            raise _fail(f"{where}.path", f"duplicate path {path!r}")
        seen_outputs.add(path)
        present = _get(item, "present", bool, where)
        if present:
            _sha(item.get("sha256"), f"{where}.sha256")
            _get(item, "size", int, where)
            _check_kind(item, where)
        elif item.get("sha256") is not None:
            raise _fail(f"{where}.sha256", "must be null when the output is not present")
        compare = _as_dict(_get(item, "compare", dict, where), f"{where}.compare")
        mode = compare.get("mode")
        if mode == "numeric":
            _finite_nonneg(compare.get("rtol"), f"{where}.compare.rtol")
            _finite_nonneg(compare.get("atol"), f"{where}.compare.atol")
            snapshot = _as_dict(compare.get("snapshot"), f"{where}.compare.snapshot")
            _rel_path(snapshot.get("path"), f"{where}.compare.snapshot.path")
            _sha(snapshot.get("sha256"), f"{where}.compare.snapshot.sha256")
            if not present:
                raise _fail(where, "a numeric output must be present")
            if item.get("kind", "file") != "file":
                raise _fail(f"{where}.kind", "a numeric output must be a file")
        elif mode != "exact":
            raise _fail(f"{where}.compare.mode", f"expected 'exact' or 'numeric', got {mode!r}")

    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    """Read and validate a manifest file."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise ManifestError(f"manifest not found: {path}") from None
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    if not path.is_file():
        raise ManifestError(f"manifest is not a regular file: {path}")
    if size > MAX_MANIFEST_BYTES:
        raise ManifestError(f"manifest is larger than {MAX_MANIFEST_BYTES} bytes: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ManifestError(f"manifest is not valid UTF-8: {exc}") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    return validate_manifest(data)


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Validate and atomically write a manifest."""
    validate_manifest(manifest)
    text = json.dumps(manifest, indent=2) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
