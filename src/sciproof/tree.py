"""Directory trees as evidence: a sorted listing of files with their hashes.

A directory is recorded as the list of its regular files (path relative to the directory,
SHA-256, size) plus one *tree digest* over that list. Empty subdirectories, permissions and
timestamps are not recorded. Symbolic links and other special files are refused, because
following them could read files outside of the directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sciproof.errors import SciProofError
from sciproof.hashing import sha256_bytes, sha256_file
from sciproof.paths import validate_relative_path

MAX_TREE_FILES = 20_000
MAX_LISTED_DIFFERENCES = 10


class TreeError(SciProofError):
    """A directory cannot be recorded or compared."""


def list_tree(root: Path) -> list[dict[str, Any]]:
    """Return ``[{"path", "sha256", "size"}, ...]`` for the regular files below ``root``."""
    files: list[dict[str, Any]] = []
    root_resolved = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(dirpath)
        for name in dirnames:
            if (directory / name).is_symlink():
                raise TreeError(f"{(directory / name).relative_to(root).as_posix()!r} is a symlink")
        for name in filenames:
            path = directory / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                raise TreeError(f"{relative!r} is not a regular file (symlinks are not supported)")
            if not path.resolve().is_relative_to(root_resolved):
                raise TreeError(f"{relative!r} resolves outside of the directory")
            if len(files) >= MAX_TREE_FILES:
                raise TreeError(f"more than {MAX_TREE_FILES} files; declare a smaller directory")
            digest, size = sha256_file(path)
            files.append({"path": relative, "sha256": digest, "size": size})
    files.sort(key=lambda item: item["path"])
    return files


def tree_digest(files: list[dict[str, Any]]) -> str:
    """SHA-256 over ``path NUL sha256 NUL size LF`` lines of the files sorted by path."""
    nul = chr(0)
    lines = [
        f"{item['path']}{nul}{item['sha256']}{nul}{item['size']}\n"
        for item in sorted(files, key=lambda item: item["path"])
    ]
    return sha256_bytes("".join(lines).encode("utf-8"))


def tree_record(root: Path) -> dict[str, Any]:
    """The manifest fields describing the directory ``root``."""
    files = list_tree(root)
    return {
        "kind": "directory",
        "sha256": tree_digest(files),
        "size": sum(item["size"] for item in files),
        "files": files,
    }


def validate_tree_files(files: Any, where: str) -> list[dict[str, Any]]:
    """Structural checks for the ``files`` list of a directory entry (raises ``ValueError``)."""
    if not isinstance(files, list):
        raise ValueError(f"{where}: expected a list")
    if len(files) > MAX_TREE_FILES:
        raise ValueError(f"{where}: more than {MAX_TREE_FILES} files")
    seen: set[str] = set()
    for index, item in enumerate(files):
        label = f"{where}[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{label}: expected an object")
        validate_relative_path(item.get("path"))
        if item["path"] in seen:
            raise ValueError(f"{label}.path: duplicate path {item['path']!r}")
        seen.add(item["path"])
        digest = item.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or digest != digest.lower():
            raise ValueError(f"{label}.sha256: expected a lowercase SHA-256 digest")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"{label}.size: expected a size >= 0")
    return files


def diff_tree(recorded: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[str]:
    """Human-readable differences between two listings (empty if identical)."""
    old = {item["path"]: item for item in recorded}
    new = {item["path"]: item for item in current}
    differences = [f"removed: {path}" for path in sorted(old.keys() - new.keys())]
    differences += [f"added: {path}" for path in sorted(new.keys() - old.keys())]
    differences += [
        f"modified: {path}"
        for path in sorted(old.keys() & new.keys())
        if old[path]["sha256"] != new[path]["sha256"]
    ]
    if len(differences) > MAX_LISTED_DIFFERENCES:
        extra = len(differences) - MAX_LISTED_DIFFERENCES
        differences = [*differences[:MAX_LISTED_DIFFERENCES], f"... and {extra} more"]
    return differences
