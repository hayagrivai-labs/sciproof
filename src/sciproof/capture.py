"""Capture an experiment: run it once and write an evidence manifest."""

from __future__ import annotations

import math
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sciproof import __version__
from sciproof.claims import claim_text, evaluate, parse_claim
from sciproof.environment import (
    hardware_info,
    interpreter_info,
    platform_info,
    selected_env_vars,
)
from sciproof.errors import CaptureError, ManifestError, SciProofError, UnsafePathError
from sciproof.gitinfo import git_info
from sciproof.hashing import sha256_file
from sciproof.manifest import (
    MANIFEST_SUFFIX,
    NAME_RE,
    SCHEMA,
    SCHEMA_VERSION,
    SCHEMA_VERSION_DIRECTORIES,
    check_nesting,
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
from sciproof.treehash import FileEntry, TreeError, scan_tree, tree_digest, tree_record

DEFAULT_MAX_LOG_BYTES = 64 * 1024
MAX_NOTE_LENGTH = 2000
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


def _scan_directory(path: Path, what: str) -> list[FileEntry]:
    try:
        return scan_tree(path)
    except TreeError as exc:
        raise CaptureError(f"{what}: {exc}") from exc


def _current_digest(path: Path, kind: str) -> str | None:
    """Digest of a file or a directory tree as it is now; ``None`` if absent or unreadable."""
    if kind == "directory":
        if not path.is_dir():
            return None
        try:
            return tree_digest(scan_tree(path))
        except TreeError:
            return None
    return sha256_file(path)[0] if path.is_file() else None


def _hash_inputs(base: Path, inputs: list[str]) -> list[dict[str, Any]]:
    records = []
    for relative in inputs:
        path = base / relative
        if path.is_dir():
            entries = _scan_directory(path, f"input {relative!r}")
            records.append({"path": relative, "kind": "directory", **tree_record(entries)})
            continue
        if not path.is_file():
            raise CaptureError(f"input file not found: {relative}")
        digest, size = sha256_file(path)
        records.append({"path": relative, "sha256": digest, "size": size})
    return records


def _numeric_directory_message(relative: str) -> str:
    return (
        f"{relative!r} is a directory; --output-numeric compares single .json, .csv, .tsv or .npy "
        "files. Declare the directory with --output (exact hashes) or its files individually."
    )


def _refuse_evidence_paths(declared: list[str], name: str) -> None:
    """A declared path must not be one of the files this capture is about to write."""
    snapshots = snapshot_dir_name(name)
    for relative in declared:
        if (
            relative == f"{name}{MANIFEST_SUFFIX}"
            or relative == snapshots
            or (relative.startswith(snapshots + "/"))
        ):
            raise CaptureError(
                f"{relative!r} is where the evidence of this capture is written; it cannot also "
                "be declared as an input or output"
            )


def _parse_claims(
    base: Path, claims: list[tuple[str, str]], output_paths: list[str]
) -> list[dict[str, Any]]:
    """Turn ``(output, expression)`` pairs into manifest claims on declared outputs."""
    records: list[dict[str, Any]] = []
    for output, expression in claims:
        try:
            relative = to_manifest_path(base, output)
        except UnsafePathError as exc:
            raise CaptureError(str(exc)) from exc
        if relative not in output_paths:
            raise CaptureError(
                f"claim on {output!r}: the file must be declared with --output or --output-numeric"
            )
        records.append(parse_claim(relative, expression))
    return records


def _check_claims_at_capture(
    base: Path, claims: list[dict[str, Any]], outputs: list[dict[str, Any]]
) -> list[str]:
    """Evaluate the claims on the outputs just produced; return warnings for those that fail."""
    present = {o["path"] for o in outputs if o.get("present") and o.get("kind", "file") == "file"}
    warnings: list[str] = []
    cache: dict[str, dict[str, object]] = {}
    for claim in claims:
        output = claim["output"]
        if output not in present:
            raise CaptureError(f"claim on {output!r}: the output file does not exist")
        if output not in cache:
            try:
                cache[output] = dict(load_numeric(base / output))
            except NumericFormatError as exc:
                raise CaptureError(f"claim on {output!r}: {exc}") from exc
        result = evaluate(claim, cache[output])
        if not result.holds:
            detail = result.message.removeprefix("does not hold: ")
            warnings.append(
                f"a claim does not hold at capture time ({claim_text(claim)}): {detail}. "
                "It is recorded anyway, and verification will report it."
            )
    return warnings


def _unique(items: list[str], what: str) -> None:
    if len(set(items)) != len(items):
        raise CaptureError(f"{what} contains the same path more than once")


def capture(
    *,
    name: str,
    argv: list[str],
    base: str | os.PathLike[str],
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    numeric_outputs: list[str] | None = None,
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
    tolerances: dict[str, dict[str, float]] | None = None,
    timeout: float | None = None,
    max_log_bytes: int = DEFAULT_MAX_LOG_BYTES,
    force: bool = False,
    claims: list[tuple[str, str]] | None = None,
    note: str | None = None,
    record_hardware: bool = False,
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
    for settings in (tolerances or {}).values():
        extra = set(settings) - {"rtol", "atol"}
        if extra:
            raise CaptureError(f"unknown tolerance setting {sorted(extra)[0]!r}; use rtol or atol")
        for value in settings.values():
            if not (math.isfinite(value) and value >= 0):
                raise CaptureError("rtol and atol must be finite numbers that are not negative")
    if timeout is not None and not (math.isfinite(timeout) and timeout > 0):
        raise CaptureError("timeout must be a positive number of seconds")
    if max_log_bytes < 0:
        raise CaptureError("max-log-bytes must not be negative")
    base = Path(base).resolve()

    try:
        input_paths = [to_manifest_path(base, p) for p in (inputs or [])]
        exact_paths = [to_manifest_path(base, p) for p in (outputs or [])]
        numeric_paths = [to_manifest_path(base, p) for p in (numeric_outputs or [])]
    except UnsafePathError as exc:
        raise CaptureError(str(exc)) from exc
    tolerance_by_path: dict[str, dict[str, float]] = {}
    for user_path, settings in (tolerances or {}).items():
        try:
            tolerance_by_path[to_manifest_path(base, user_path)] = settings
        except UnsafePathError as exc:
            raise CaptureError(str(exc)) from exc
    for relative in tolerance_by_path:
        if relative not in numeric_paths:
            raise CaptureError(
                f"tolerance for {relative!r}: the file must be declared with --output-numeric"
            )
    _unique(input_paths, "--input")
    _unique(exact_paths + numeric_paths, "--output/--output-numeric")
    _refuse_evidence_paths(input_paths + exact_paths + numeric_paths, name)
    claim_records = _parse_claims(base, claims or [], exact_paths + numeric_paths)
    if note is not None and (not note.strip() or len(note) > MAX_NOTE_LENGTH or "\x00" in note):
        raise CaptureError(
            f"the note must be 1 to {MAX_NOTE_LENGTH} characters of text (no NUL bytes)"
        )
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
    input_directories = [r["path"] for r in input_records if r.get("kind") == "directory"]
    for relative in input_paths + exact_paths + numeric_paths:
        for directory in input_directories:
            if relative != directory and relative.startswith(directory + "/"):
                raise CaptureError(
                    f"{relative!r} lies inside the declared input directory {directory!r}; "
                    "declare either the directory or the file"
                )
    before: dict[str, str | None] = {}
    for relative in exact_paths + numeric_paths:
        target = base / relative
        if target.is_dir():
            if relative in numeric_paths:
                raise CaptureError(_numeric_directory_message(relative))
            entries = _scan_directory(target, f"output {relative!r}")
            before[relative] = tree_digest(entries)
        else:
            before[relative] = sha256_file(target)[0] if target.is_file() else None

    large = (
        "a relative tolerance this large accepts values that differ by 100% or more (any number "
        "is then equivalent to 0, and a value to its negation when rtol >= 2). Use a smaller "
        "rtol unless that is intended"
    )
    uses_default = any("rtol" not in tolerance_by_path.get(r, {}) for r in numeric_paths)
    if uses_default and rtol >= 1:
        warnings.append(f"rtol={rtol} is 1 or more: {large}")
    for relative in numeric_paths:
        own = tolerance_by_path.get(relative, {}).get("rtol")
        if own is not None and own >= 1:
            warnings.append(f"rtol={own} for {relative!r} is 1 or more: {large}")

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
        digest = _current_digest(base / record["path"], record.get("kind", "file"))
        if digest != record["sha256"]:
            raise CaptureError(
                f"the command modified or removed the declared input {record['path']!r}; "
                "inputs must be read-only. No evidence was written."
            )

    # Snapshots are written to a staging directory and only replace the old ones once the new
    # manifest has been written, so a failed capture never damages earlier evidence and
    # --force never leaves snapshots that the new manifest does not reference.
    snapshot_dir = base / snapshot_dir_name(name)
    staging = base / f"{snapshot_dir_name(name)}.tmp"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        output_records: list[dict[str, Any]] = []
        for relative in exact_paths + numeric_paths:
            numeric = relative in numeric_paths
            target = base / relative
            record: dict[str, Any] = {"path": relative}
            if target.is_dir():
                if numeric:
                    raise CaptureError(_numeric_directory_message(relative))
                entries = _scan_directory(target, f"output {relative!r}")
                tree = tree_record(entries)
                record.update(present=True, kind="directory", **tree)
                if before[relative] == tree["sha256"]:
                    warnings.append(
                        f"output {relative!r} existed before the run and was not changed by it"
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
                staging.mkdir(exist_ok=True)
                snapshot_name = f"{len(output_records)}-{target.name}"
                shutil.copyfile(target, staging / snapshot_name)
                snapshot_digest, _ = sha256_file(staging / snapshot_name)
                record["compare"] = {
                    "mode": "numeric",
                    "rtol": tolerance_by_path.get(relative, {}).get("rtol", rtol),
                    "atol": tolerance_by_path.get(relative, {}).get("atol", atol),
                    "snapshot": {
                        "path": f"{snapshot_dir_name(name)}/{snapshot_name}",
                        "sha256": snapshot_digest,
                    },
                }
            else:
                record["compare"] = {"mode": "exact"}
            output_records.append(record)

        declared = [(r["path"], r.get("kind", "file")) for r in input_records + output_records]
        try:
            check_nesting(declared)
        except ManifestError as exc:
            raise CaptureError(str(exc).removeprefix("invalid manifest: manifest: ")) from exc
        has_directory = any(kind == "directory" for _, kind in declared)
        warnings.extend(_check_claims_at_capture(base, claim_records, output_records))
        manifest: dict[str, Any] = {
            "schema": SCHEMA,
            "schema_version": (
                SCHEMA_VERSION_DIRECTORIES
                if has_directory or claim_records or note or record_hardware
                else SCHEMA_VERSION
            ),
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
                **({"hardware": hardware_info()} if record_hardware else {}),
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
        if claim_records:
            manifest["claims"] = claim_records
        if note:
            manifest["note"] = note
        try:
            write_manifest(manifest_path, manifest)
        except OSError as exc:
            raise CaptureError(f"cannot write {manifest_path}: {exc}") from exc
        except SciProofError as exc:  # a bug in SciProof itself; never write an invalid manifest
            raise CaptureError(f"internal error: generated an invalid manifest: {exc}") from exc
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        if staging.exists():
            os.replace(staging, snapshot_dir)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return CaptureResult(manifest_path=manifest_path, manifest=manifest, warnings=warnings)
