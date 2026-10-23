"""Record: capture an experiment without declaring anything.

``sciproof capture`` needs to be told which files are inputs and which are outputs, and whether an
output may be compared exactly or only within a tolerance. ``sciproof record`` finds that out by
running the command, and then captures it with what it found:

1. The command is run once with Python's audit hook installed (as ``suggest-inputs`` does). The
   files it **read** inside the working directory are the inputs; the files it **created or
   changed** are the outputs. Files below ``.git``, virtual environments, caches and SciProof's
   own files are never considered.
2. Unless ``--fast``, it is run a second time from the same starting state. An output that is
   identical both times is declared; a JSON, CSV or TSV output that differs only in its numbers is
   declared with a tolerance that covers the difference (ten times the spread, as ``calibrate``
   suggests); any other output that differs is not declared at all, because an exact hash of it
   could never be verified, and it is listed so that nothing is silently left out.
3. The command is captured for real, with those declarations.

So the command runs three times (twice with ``--fast`` skipping the second). Files that the
command created are removed between the runs so that each run starts from the same state; a file
that existed before and was rewritten cannot be restored and is recorded as an output.

This finds what Python code opened. Reads by C extensions through their own handles, by other
programs and over the network are invisible, and for a command that is not Python no input can
be detected at all: ``--input`` adds files by hand. What is recorded is a proposal that the report
shows in full; check it before relying on it.
"""

from __future__ import annotations

import contextlib
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sciproof.calibrate import _round_up, _spread
from sciproof.capture import DEFAULT_MAX_LOG_BYTES, CaptureResult, capture
from sciproof.errors import CaptureError
from sciproof.hashing import sha256_file
from sciproof.manifest import MANIFEST_SUFFIX, NAME_RE
from sciproof.numeric import Leaf, NumericFormatError, load_numeric
from sciproof.paths import to_manifest_path
from sciproof.runner import CommandInterrupted, CommandNotRunnable, CommandTimeout, run_command
from sciproof.suggest import suggest_inputs

IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".ipynb_checkpoints",
    }
)
MAX_SCANNED_FILES = 50_000
MAX_DECLARED = 500
DEFAULT_RTOL = 1e-9
SAFETY_FACTOR = 10
NUMERIC_SUFFIXES = (".json", ".csv", ".tsv")


@dataclass
class RecordResult:
    capture: CaptureResult
    inputs: list[str]
    exact: list[str]
    numeric: list[str]
    tolerances: dict[str, dict[str, float]]
    excluded: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    command_line: str = ""


def _ignored(parts: tuple[str, ...]) -> bool:
    return any(
        part in IGNORED_DIRECTORIES or ".sciproof" in part or part.startswith(".sciproof-rerun-")
        for part in parts
    )


def _snapshot(base: Path) -> dict[str, tuple[int, int]]:
    """``{relative path: (size, mtime_ns)}`` of every regular file below ``base`` (no links)."""
    found: dict[str, tuple[int, int]] = {}
    for current, directories, files in os.walk(base, followlinks=False):
        relative = Path(current).relative_to(base)
        directories[:] = sorted(d for d in directories if not _ignored((*relative.parts, d)))
        for name in files:
            path = Path(current) / name
            if path.is_symlink() or _ignored((*relative.parts, name)):
                continue
            try:
                info = path.stat()
            except OSError:
                continue
            found[(relative / name).as_posix()] = (info.st_size, info.st_mtime_ns)
            if len(found) > MAX_SCANNED_FILES:
                raise CaptureError(
                    f"more than {MAX_SCANNED_FILES} files below the working directory; run "
                    "'record' in a smaller directory or use 'capture' with declared files"
                )
    return found


def _changed(
    before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]
) -> tuple[list[str], list[str]]:
    created = sorted(p for p in after if p not in before)
    modified = sorted(p for p in after if p in before and after[p] != before[p])
    return created, modified


def _remove_created(base: Path, created: list[str]) -> None:
    """Delete files the command created, and the directories that became empty because of it."""
    directories: set[Path] = set()
    for relative in created:
        path = base / relative
        path.unlink(missing_ok=True)
        directories.update(p for p in path.parents if p != base and base in p.parents)
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        with contextlib.suppress(OSError):
            directory.rmdir()  # only succeeds if it is empty


def _leaves(path: Path) -> list[Leaf] | None:
    if path.suffix.lower() not in NUMERIC_SUFFIXES:
        return None
    try:
        return load_numeric(path)
    except NumericFormatError:
        return None


def _run(argv: list[str], base: Path, timeout: float | None) -> int:
    try:
        return run_command(argv, base, timeout).exit_code
    except CommandInterrupted as exc:
        raise CaptureError("interrupted; nothing was recorded") from exc
    except CommandTimeout as exc:
        raise CaptureError(f"{exc}; nothing was recorded") from exc
    except CommandNotRunnable as exc:
        raise CaptureError(str(exc)) from exc


def record(
    *,
    name: str,
    argv: list[str],
    base: Path | str,
    inputs: list[str] | None = None,
    exclude: list[str] | None = None,
    fast: bool = False,
    timeout: float | None = None,
    max_log_bytes: int = DEFAULT_MAX_LOG_BYTES,
    force: bool = False,
    note: str | None = None,
    record_hardware: bool = False,
) -> RecordResult:
    """Find the inputs and outputs of ``argv`` by running it, then capture it with them."""
    if not NAME_RE.fullmatch(name):
        raise CaptureError(
            f"invalid name {name!r}: use letters, digits, '.', '_' or '-' (max 100 characters)"
        )
    if not argv or not argv[0]:
        raise CaptureError("no command given")
    base = Path(base).resolve()
    if (base / f"{name}{MANIFEST_SUFFIX}").exists() and not force:
        raise CaptureError(f"{name}{MANIFEST_SUFFIX} already exists (use --force to overwrite it)")
    extra_inputs = [to_manifest_path(base, p) for p in inputs or []]
    skipped = [to_manifest_path(base, p) for p in exclude or []]

    def is_skipped(relative: str) -> bool:
        return any(relative == s or relative.startswith(s + "/") for s in skipped)

    before = _snapshot(base)
    found = suggest_inputs(argv, base, [], timeout)
    if found.exit_code != 0:
        raise CaptureError(
            f"the command exited with status {found.exit_code}; nothing was recorded "
            "(record needs a run that succeeds)"
        )
    after_first = _snapshot(base)
    created, modified = _changed(before, after_first)
    written = [p for p in created + modified if not is_skipped(p)]
    if not written:
        raise CaptureError(
            "the command created or changed no file below the working directory, so there is "
            "nothing to record; declare the outputs with 'sciproof capture'"
        )
    first_digest = {p: sha256_file(base / p)[0] for p in written}
    first_leaves = {p: _leaves(base / p) for p in written}
    _remove_created(base, created)

    notes: list[str] = []
    excluded: list[tuple[str, str]] = []
    second_digest: dict[str, str] = {}
    second_leaves: dict[str, list[Leaf] | None] = {}
    if not fast:
        if _run(argv, base, timeout) != 0:
            raise CaptureError("the second run of the command failed; nothing was recorded")
        after_second = _snapshot(base)
        created_again, modified_again = _changed(before, after_second)
        for relative in created_again + modified_again:
            if relative in first_digest and (base / relative).is_file():
                second_digest[relative] = sha256_file(base / relative)[0]
                second_leaves[relative] = _leaves(base / relative)
        for relative in created_again + modified_again:
            if relative not in first_digest and not is_skipped(relative):
                notes.append(f"{relative} was written by the second run only; it is not declared")
        _remove_created(base, created_again)
    else:
        notes.append("determinism was not checked (--fast): every output is assumed stable")

    exact: list[str] = []
    numeric: list[str] = []
    tolerances: dict[str, dict[str, float]] = {}
    for relative in written:
        parsed = first_leaves[relative]
        if fast or second_digest.get(relative) == first_digest[relative]:
            (numeric if parsed is not None else exact).append(relative)
        elif relative not in second_digest:
            excluded.append((relative, "not written by the second run"))
        elif parsed is None or second_leaves.get(relative) is None:
            excluded.append(
                (relative, "differs between two identical runs and is not JSON/CSV/TSV")
            )
        else:
            _, max_rel, _, problem = _spread([parsed, second_leaves[relative] or []], 0.0, 0.0)
            if problem:
                excluded.append((relative, f"differs between two identical runs: {problem}"))
            else:
                numeric.append(relative)
                tolerances[relative] = {
                    "rtol": max(DEFAULT_RTOL, _round_up(max_rel * SAFETY_FACTOR))
                }

    declared_outputs = set(exact) | set(numeric)
    never_inputs = set(first_digest) | set(second_digest)
    detected = [p for p in found.inside if p not in never_inputs and not is_skipped(p)]
    chosen_inputs = sorted(
        {p for p in detected + extra_inputs if (base / p).is_file() and p not in declared_outputs}
    )
    if not found.hook_loaded:
        notes.append(
            "no input could be detected: the command is not run by Python (or Python ran with -I "
            "or -S). Declare inputs with --input."
        )
    if len(chosen_inputs) > MAX_DECLARED or len(declared_outputs) > MAX_DECLARED:
        raise CaptureError(
            f"more than {MAX_DECLARED} inputs or outputs were found; declare a directory with "
            "'sciproof capture --input DIR' instead"
        )
    if not declared_outputs and not chosen_inputs:
        raise CaptureError("every output was excluded and no input was found; nothing to record")

    result = capture(
        name=name,
        argv=argv,
        base=base,
        inputs=chosen_inputs,
        outputs=exact,
        numeric_outputs=numeric,
        rtol=DEFAULT_RTOL,
        tolerances=tolerances or None,
        timeout=timeout,
        max_log_bytes=max_log_bytes,
        force=force,
        note=note,
        record_hardware=record_hardware,
    )
    return RecordResult(
        capture=result,
        inputs=chosen_inputs,
        exact=sorted(exact),
        numeric=sorted(numeric),
        tolerances=tolerances,
        excluded=excluded,
        notes=notes,
        command_line=_command_line(name, argv, chosen_inputs, exact, numeric, tolerances),
    )


def _command_line(
    name: str,
    argv: list[str],
    inputs: list[str],
    exact: list[str],
    numeric: list[str],
    tolerances: dict[str, dict[str, Any]],
) -> str:
    parts = ["sciproof", "capture", "--name", name]
    for flag, paths in (("--input", inputs), ("--output", exact), ("--output-numeric", numeric)):
        for path in sorted(paths):
            parts += [flag, path]
    for path, settings in sorted(tolerances.items()):
        parts += ["--tolerance", path, " ".join(f"{k}={v:g}" for k, v in settings.items())]
    return shlex.join([*parts, "--", *argv])
