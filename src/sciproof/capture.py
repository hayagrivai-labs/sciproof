"""Capture an experiment: run it once and write an evidence manifest."""

from __future__ import annotations

import math
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sciproof import __version__
from sciproof.environment import interpreter_info, platform_info, selected_env_vars
from sciproof.errors import CaptureError, SciProofError, UnsafePathError
from sciproof.gitinfo import git_info
from sciproof.hashing import sha256_file
from sciproof.manifest import (
    MANIFEST_SUFFIX,
    NAME_RE,
    SCHEMA,
    SCHEMA_VERSION,
    SCHEMA_VERSION_DIRECTORIES,
    snapshot_dir_name,
    write_manifest,
)
from sciproof.numeric import NumericFormatError, load_numeric
from sciproof.paths import to_manifest_path
from sciproof.runner import (
    CommandInterrupted,
    CommandNotRunnable,
    CommandTimeout,
    run_command,
    summarize_stream,
)
from sciproof.tree import TreeError, tree_record

DEFAULT_MAX_LOG_BYTES = 64 * 1024
DEFAULT_RTOL = 1e-9
DEFAULT_ATOL = 0.0


@dataclass
class CaptureResult:
    manifest_path: Path
    manifest: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return int(self.manifest["execution"]["exit_code"])


def _hash_inputs(base: Path, inputs: list[str]) -> list[dict[str, Any]]:
    records = []
    for relative in inputs:
        path = base / relative
        if path.is_dir():
            records.append({"path": relative, **_tree(path, f"input {relative!r}")})
            continue
        if not path.is_file():
            raise CaptureError(f"input file not found: {relative}")
        digest, size = sha256_file(path)
        records.append({"path": relative, "sha256": digest, "size": size})
    return records


def _tree(path: Path, what: str) -> dict[str, Any]:
    try:
        return tree_record(path)
    except TreeError as exc:
        raise CaptureError(f"{what}: {exc}") from exc


def _digest_now(path: Path) -> str | None:
    """Digest of a file or directory tree, ``None`` if it does not exist or cannot be listed."""
    if path.is_file():
        return sha256_file(path)[0]
    if path.is_dir():
        try:
            return tree_record(path)["sha256"]
        except TreeError:
            return None
    return None


def _unique(items: list[str], what: str) -> None:
    if len(set(items)) != len(items):
        raise CaptureError(f"{what} contains the same path more than once")


def capture(
    *,
    name: str,
    argv: list[str],
    base: Path,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    numeric_outputs: list[str] | None = None,
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
    timeout: float | None = None,
    max_log_bytes: int = DEFAULT_MAX_LOG_BYTES,
    force: bool = False,
) -> CaptureResult:
    """Run ``argv`` once in ``base`` and write ``<name>.sciproof.json`` there.

    Paths in ``inputs``/``outputs``/``numeric_outputs`` are user paths relative to ``base``
    (or absolute paths inside it). The manifest is only written if everything succeeded;
    a command that exits with a nonzero status is still recorded.
    """
    if not NAME_RE.fullmatch(name):
        raise CaptureError(
            f"invalid name {name!r}: use letters, digits, '.', '_' or '-' (max 100 characters)"
        )
    if not argv or not argv[0]:
        raise CaptureError("no command given")
    if not (math.isfinite(rtol) and math.isfinite(atol)) or rtol < 0 or atol < 0:
        raise CaptureError("rtol and atol must be finite numbers that are not negative")
    if timeout is not None and not (math.isfinite(timeout) and timeout > 0):
        raise CaptureError("timeout must be a positive number of seconds")
    if max_log_bytes < 0:
        raise CaptureError("max-log-bytes must not be negative")
    base = base.resolve()

    try:
        input_paths = [to_manifest_path(base, p) for p in (inputs or [])]
        exact_paths = [to_manifest_path(base, p) for p in (outputs or [])]
        numeric_paths = [to_manifest_path(base, p) for p in (numeric_outputs or [])]
    except UnsafePathError as exc:
        raise CaptureError(str(exc)) from exc
    _unique(input_paths, "--input")
    _unique(exact_paths + numeric_paths, "--output/--output-numeric")
    overlap = set(input_paths) & set(exact_paths + numeric_paths)
    if overlap:
        raise CaptureError(f"a file cannot be both input and output: {sorted(overlap)[0]}")
    if not (input_paths or exact_paths or numeric_paths):
        raise CaptureError(
            "declare at least one --input, --output or --output-numeric file; "
            "SciProof only verifies files that were declared"
        )

    manifest_path = base / f"{name}{MANIFEST_SUFFIX}"
    if manifest_path.exists() and not force:
        raise CaptureError(f"{manifest_path.name} already exists (use --force to overwrite it)")

    warnings: list[str] = []
    input_records = _hash_inputs(base, input_paths)
    before = {}
    for relative in exact_paths + numeric_paths:
        target = base / relative
        if target.is_dir() and relative in numeric_paths:
            raise CaptureError(
                f"numeric output {relative!r} is a directory; --output-numeric needs a .json or "
                ".csv file (use --output for a whole directory)"
            )
        before[relative] = _digest_now(target)

    git = git_info(base)
    if git and git["dirty"]:
        warnings.append(
            "the Git working tree has uncommitted changes, so the commit alone does not "
            "identify the code that was run"
        )
    interpreter, interpreter_source = interpreter_info(argv[0], base)

    try:
        result = run_command(argv, base, timeout)
    except CommandInterrupted as exc:
        raise CaptureError("interrupted; no evidence was written") from exc
    except CommandTimeout as exc:
        raise CaptureError(f"{exc}; no evidence was written") from exc
    except CommandNotRunnable as exc:
        raise CaptureError(str(exc)) from exc

    # The command must treat declared inputs as read-only, otherwise the evidence is meaningless.
    for record in input_records:
        if _digest_now(base / record["path"]) != record["sha256"]:
            raise CaptureError(
                f"the command modified or removed the declared input {record['path']!r}; "
                "inputs must be read-only. No evidence was written."
            )

    snapshots = base / snapshot_dir_name(name)
    output_records: list[dict[str, Any]] = []
    for relative in exact_paths + numeric_paths:
        numeric = relative in numeric_paths
        target = base / relative
        record: dict[str, Any] = {"path": relative}
        if target.is_dir() and not numeric:
            record.update(present=True, **_tree(target, f"output {relative!r}"))
            if before[relative] == record["sha256"]:
                warnings.append(
                    f"output {relative!r} existed before the run and was not changed by it"
                )
        elif target.is_dir():
            raise CaptureError(
                f"numeric output {relative!r} is a directory; --output-numeric needs a file"
            )
        elif target.is_file():
            digest, size = sha256_file(target)
            record.update(present=True, sha256=digest, size=size)
            if before[relative] == digest:
                warnings.append(
                    f"output {relative!r} existed before the run and was not changed by it"
                )
        elif result.exit_code == 0:
            raise CaptureError(
                f"the command succeeded but the declared output {relative!r} does not exist. "
                "No evidence was written."
            )
        else:
            record.update(present=False, sha256=None, size=0)
            if numeric:
                raise CaptureError(
                    f"the command failed and the numeric output {relative!r} does not exist"
                )

        if numeric:
            try:
                load_numeric(target)
            except NumericFormatError as exc:
                raise CaptureError(str(exc)) from exc
            snapshots.mkdir(exist_ok=True)
            snapshot_name = f"{len(output_records)}-{target.name}"
            shutil.copyfile(target, snapshots / snapshot_name)
            snapshot_digest, _ = sha256_file(snapshots / snapshot_name)
            record["compare"] = {
                "mode": "numeric",
                "rtol": rtol,
                "atol": atol,
                "snapshot": {
                    "path": f"{snapshot_dir_name(name)}/{snapshot_name}",
                    "sha256": snapshot_digest,
                },
            }
        else:
            record["compare"] = {"mode": "exact"}
        output_records.append(record)

    uses_directories = any(
        item.get("kind") == "directory" for item in [*input_records, *output_records]
    )
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION_DIRECTORIES if uses_directories else SCHEMA_VERSION,
        "name": name,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "tool": {"name": "sciproof", "version": __version__},
        "command": {"argv": list(argv), "cwd": "."},
        "environment": {
            "source": interpreter_source,
            "python": {
                "version": interpreter["version"],
                "implementation": interpreter["implementation"],
                "byteorder": interpreter.get("byteorder", ""),
            },
            "platform": platform_info(),
            "env_vars": selected_env_vars(),
        },
        "dependencies": [
            {"name": pkg_name, "version": pkg_version}
            for pkg_name, pkg_version in interpreter["packages"]
        ],
        "git": git,
        "inputs": input_records,
        "execution": {
            "exit_code": result.exit_code,
            "duration_seconds": round(result.duration_seconds, 3),
            "stdout": summarize_stream(result.stdout, max_log_bytes),
            "stderr": summarize_stream(result.stderr, max_log_bytes),
        },
        "outputs": output_records,
    }
    try:
        write_manifest(manifest_path, manifest)
    except OSError as exc:
        raise CaptureError(f"cannot write {manifest_path}: {exc}") from exc
    except SciProofError as exc:  # a bug in SciProof itself; never write an invalid manifest
        raise CaptureError(f"internal error: generated an invalid manifest: {exc}") from exc
    return CaptureResult(manifest_path=manifest_path, manifest=manifest, warnings=warnings)
