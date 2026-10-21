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

from sciproof.claims import (
    MAX_CLAIMS,
    OPS,
    ORDER_OPS,
    SUPPORTED_SUFFIXES,
    is_number,
    location_matches_file,
)
from sciproof.environment import MAX_GPUS
from sciproof.errors import ManifestError, UnsafePathError
from sciproof.jsonstrict import reject_duplicate_keys
from sciproof.paths import validate_relative_path
from sciproof.treehash import MAX_TREE_FILES, tree_digest, tree_size

SCHEMA = "sciproof.manifest"
SCHEMA_VERSION = "1.0"
# Written instead when the manifest contains a directory artifact (see docs/MANIFEST.md).
SCHEMA_VERSION_DIRECTORIES = "1.1"
SUPPORTED_MAJOR = 1
MANIFEST_SUFFIX = ".sciproof.json"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_NOTE_LENGTH = 2000

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


def _size(obj: dict[str, Any], where: str) -> None:
    if _get(obj, "size", int, where) < 0:
        raise _fail(f"{where}.size", "expected a size >= 0")


def _sha(value: Any, where: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise _fail(where, "expected a lowercase 64-character hex SHA-256 digest")
    return value


def _rel_path(value: Any, where: str) -> str:
    try:
        return validate_relative_path(value)
    except UnsafePathError as exc:
        raise _fail(where, str(exc)) from exc


def _check_kind(item: dict[str, Any], where: str, minor: int, present: bool) -> str:
    """Validate ``kind`` and, for a directory, its file list. Returns the kind."""
    kind = item.get("kind", "file")
    if kind == "file":
        if "files" in item:
            raise _fail(f"{where}.files", "only a directory has a list of files")
        return "file"
    if kind != "directory":
        raise _fail(f"{where}.kind", f"expected 'file' or 'directory', got {kind!r}")
    if minor < 1:
        raise _fail(f"{where}.kind", "directory entries need schema_version 1.1 or later")
    files = item.get("files")
    if not present:
        if files:
            raise _fail(f"{where}.files", "must be empty when the directory is not present")
        return "directory"
    if not isinstance(files, list):
        raise _fail(f"{where}.files", "expected a list of files")
    if len(files) > MAX_TREE_FILES:
        raise _fail(f"{where}.files", f"more than {MAX_TREE_FILES} files")
    seen: set[str] = set()
    for index, entry in enumerate(files):
        inner = f"{where}.files[{index}]"
        entry = _as_dict(entry, inner)
        path = _rel_path(entry.get("path"), f"{inner}.path")
        if path in seen:
            raise _fail(f"{inner}.path", f"duplicate path {path!r}")
        seen.add(path)
        _sha(entry.get("sha256"), f"{inner}.sha256")
        _size(entry, inner)
    if tree_digest(files) != item["sha256"]:
        raise _fail(f"{where}.sha256", "does not match the digest of the listed files")
    if tree_size(files) != item["size"]:
        raise _fail(f"{where}.size", "does not match the sizes of the listed files")
    return "directory"


def _check_note(note: Any, minor: int) -> None:
    """Validate the optional free-text ``note`` (schema 1.1)."""
    if note is None:
        return
    if minor < 1:
        raise _fail("note", "a note needs schema_version 1.1 or later")
    if (
        not isinstance(note, str)
        or not note.strip()
        or len(note) > MAX_NOTE_LENGTH
        or "\x00" in note
    ):
        raise _fail("note", f"expected 1 to {MAX_NOTE_LENGTH} characters of text")


def _check_hardware(hardware: Any, minor: int) -> None:
    """Validate the optional ``environment.hardware`` record (schema 1.1)."""
    if hardware is None:
        return
    where = "environment.hardware"
    if minor < 1:
        raise _fail(where, "hardware needs schema_version 1.1 or later")
    hardware = _as_dict(hardware, where)
    # The key must be present, like the other hardware fields: readers
    # (inspect, diff, verify) index it directly, and hardware_info() always
    # writes it. The value may still be null (os.cpu_count() can return None).
    if "cpu_count" not in hardware:
        raise _fail(f"{where}.cpu_count", "expected a positive integer or null")
    cpus = hardware["cpu_count"]
    if cpus is not None and (isinstance(cpus, bool) or not isinstance(cpus, int) or cpus < 1):
        raise _fail(f"{where}.cpu_count", "expected a positive integer or null")
    processor = hardware.get("processor")
    if not isinstance(processor, str) or len(processor) > 200:
        raise _fail(f"{where}.processor", "expected a string of at most 200 characters")
    if hardware.get("gpu_probe") not in ("nvidia-smi", "none", "failed"):
        raise _fail(f"{where}.gpu_probe", "expected 'nvidia-smi', 'none' or 'failed'")
    gpus = hardware.get("gpus")
    if not isinstance(gpus, list) or len(gpus) > MAX_GPUS:
        raise _fail(f"{where}.gpus", f"expected a list of at most {MAX_GPUS} GPUs")
    for index, gpu in enumerate(gpus):
        gpu = _as_dict(gpu, f"{where}.gpus[{index}]")
        for key, limit in (("name", 100), ("driver", 40)):
            value = gpu.get(key)
            if not isinstance(value, str) or len(value) > limit:
                raise _fail(f"{where}.gpus[{index}].{key}", f"expected a string of at most {limit}")
        memory = gpu.get("memory_mib")
        if memory is not None and (
            isinstance(memory, bool) or not isinstance(memory, int) or memory < 0
        ):
            raise _fail(
                f"{where}.gpus[{index}].memory_mib", "expected a non-negative integer or null"
            )


def _finite_number(value: Any) -> bool:
    # A Python int is exact and always finite, but math.isfinite() converts to
    # float and raises OverflowError beyond ~1.8e308 (#88). The == and ordering
    # operators compare such ints exactly, so a claim value this large is a
    # valid number, not an error.
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    return is_number(value) and math.isfinite(value)


def _check_claims(claims: Any, minor: int, outputs: dict[str, tuple[str, bool]]) -> None:
    """Validate the optional ``claims`` list (schema 1.1): see ``sciproof.claims``."""
    if claims is None:
        return
    if minor < 1:
        raise _fail("claims", "claims need schema_version 1.1 or later")
    if not isinstance(claims, list):
        raise _fail("claims", f"expected a list, got {type(claims).__name__}")
    if len(claims) > MAX_CLAIMS:
        raise _fail("claims", f"more than {MAX_CLAIMS} claims")
    for index, claim in enumerate(claims):
        where = f"claims[{index}]"
        claim = _as_dict(claim, where)
        output = _rel_path(claim.get("output"), f"{where}.output")
        kind, present = outputs.get(output, (None, False))
        if kind != "file" or not present or not output.lower().endswith(SUPPORTED_SUFFIXES):
            raise _fail(
                f"{where}.output",
                f"{output!r} must be a declared .json or .csv output file that was present",
            )
        location = claim.get("location")
        if not isinstance(location, str) or not location_matches_file(location, output):
            raise _fail(f"{where}.location", f"{location!r} is not a location in {output!r}")
        op = claim.get("op")
        if op not in OPS:
            raise _fail(f"{where}.op", f"expected one of {list(OPS)}, got {op!r}")
        value = claim.get("value")
        if op in ORDER_OPS or op == "~=":
            if not _finite_number(value):
                raise _fail(f"{where}.value", f"{op} needs a finite number")
        elif not (value is None or isinstance(value, (bool, str)) or _finite_number(value)) or (
            isinstance(value, str) and len(value) > 1000
        ):
            raise _fail(f"{where}.value", "expected a number, string, boolean or null")
        if op == "~=":
            _finite_nonneg(claim.get("rtol"), f"{where}.rtol")
            _finite_nonneg(claim.get("atol"), f"{where}.atol")
        elif "rtol" in claim or "atol" in claim:
            raise _fail(where, "rtol and atol only apply to '~='")


def check_nesting(declared: list[tuple[str, str]]) -> None:
    """A declared path must not lie inside another declared directory."""
    directories = [path for path, kind in declared if kind == "directory"]
    for path, _ in declared:
        for directory in directories:
            if path != directory and path.startswith(directory + "/"):
                raise _fail(
                    "manifest",
                    f"{path!r} lies inside the declared directory {directory!r}; declare "
                    "either the directory or the file",
                )


def _finite_nonneg(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(where, "expected a number")
    if isinstance(value, int):
        # Exact ints never fail math.isfinite(), but returning them as float
        # can overflow; a manifest is untrusted input, so report a value that
        # no float can carry as invalid instead of crashing every command
        # that loads it (#88).
        if value < 0:
            raise _fail(where, "expected a finite number >= 0")
        try:
            return float(value)
        except OverflowError:
            raise _fail(where, "expected a finite number >= 0") from None
    if not math.isfinite(value) or value < 0:
        raise _fail(where, "expected a finite number >= 0")
    return float(value)


def _validate_stream(stream: Any, where: str) -> None:
    stream = _as_dict(stream, where)
    _sha(stream.get("sha256"), f"{where}.sha256")
    _size(stream, where)
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
    minor = int(match.group(2))
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
    _check_hardware(environment.get("hardware"), minor)
    _check_note(manifest.get("note"), minor)

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

    declared: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(_get(manifest, "inputs", list, "manifest")):
        where = f"inputs[{index}]"
        item = _as_dict(item, where)
        path = _rel_path(item.get("path"), f"{where}.path")
        if path in seen:
            raise _fail(f"{where}.path", f"duplicate path {path!r}")
        seen.add(path)
        _sha(item.get("sha256"), f"{where}.sha256")
        _size(item, where)
        declared.append((path, _check_kind(item, where, minor, present=True)))

    execution = _as_dict(_get(manifest, "execution", dict, "manifest"), "execution")
    _get(execution, "exit_code", int, "execution")
    _finite_nonneg(execution.get("duration_seconds"), "execution.duration_seconds")
    _validate_stream(execution.get("stdout"), "execution.stdout")
    _validate_stream(execution.get("stderr"), "execution.stderr")

    seen_outputs: set[str] = set()
    output_info: dict[str, tuple[str, bool]] = {}
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
            _size(item, where)
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
        elif mode != "exact":
            raise _fail(f"{where}.compare.mode", f"expected 'exact' or 'numeric', got {mode!r}")
        kind = _check_kind(item, where, minor, present)
        if kind == "directory" and mode != "exact":
            raise _fail(f"{where}.compare.mode", "a directory can only be compared exactly")
        declared.append((path, kind))
        output_info[path] = (kind, present)

    _check_claims(manifest.get("claims"), minor, output_info)
    check_nesting(declared)
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
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    except UnicodeDecodeError as exc:
        raise ManifestError(f"manifest is not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    except (ValueError, RecursionError) as exc:  # includes JSONDecodeError and duplicate keys
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    return validate_manifest(data)


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Validate and atomically write a manifest."""
    validate_manifest(manifest)
    text = json.dumps(manifest, indent=2) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    # Bytes, not text mode: the file must be identical on every OS (LF only), because hashes and
    # signatures cover its exact bytes.
    temporary.write_bytes(text.encode("utf-8"))
    os.replace(temporary, path)
