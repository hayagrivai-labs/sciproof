"""Calibrate: run the recorded command several times and measure how much its outputs vary.

A tolerance is a guess unless someone has looked at the noise. ``sciproof calibrate`` executes the
recorded command ``--runs`` times (like ``verify --rerun``, so it needs the same confirmation),
collects every declared output after each run, and compares the runs with each other and with
the recorded baseline:

* an output compared **exactly** must have the same content in every run, otherwise it is not
  deterministic and an exact hash can never be verified;
* a **numeric** output is measured: the largest absolute and relative spread of any number, whether
  that is inside the tolerance recorded in the manifest, and a tolerance that would have covered
  what was seen (ten times the largest relative spread: a handful of runs only samples the noise).

The outputs that existed before are moved aside and put back afterwards, so the working tree is
left as it was and the manifest is not changed.
"""

from __future__ import annotations

import contextlib
import math
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sciproof.errors import ConfirmationRequired, UsageError
from sciproof.hashing import sha256_file
from sciproof.manifest import load_manifest
from sciproof.numeric import Leaf, NumericFormatError, load_numeric
from sciproof.paths import resolve_inside
from sciproof.runner import run_command
from sciproof.textutil import safe_text
from sciproof.treehash import TreeError, scan_tree, tree_digest
from sciproof.update import _backup_and_remove, _restore

MIN_RUNS = 2
MAX_RUNS = 50
SAFETY_FACTOR = 10  # the suggested rtol is this many times the largest relative spread seen
_EXACT_INT_LIMIT = 2**53  # integers up to here convert to float without losing anything


@dataclass
class OutputSpread:
    """What the runs showed for one declared output."""

    path: str
    mode: str  # "exact" or "numeric"
    ok: bool
    message: str
    max_abs: float = 0.0
    max_rel: float = 0.0
    suggestion: str | None = None


@dataclass
class CalibrationReport:
    name: str
    runs: int
    recorded_exit_code: int
    exit_codes: list[int]
    outputs: list[OutputSpread] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(o.ok for o in self.outputs) and all(
            code == self.recorded_exit_code for code in self.exit_codes
        )

    def to_text(self) -> str:
        lines = [
            f"SciProof calibration of '{safe_text(self.name)}' "
            f"({self.runs} runs of the recorded command, plus the recorded baseline)",
            "",
        ]
        wrong = sorted({c for c in self.exit_codes if c != self.recorded_exit_code})
        if wrong:
            lines.append(
                f"  [DIFFERS] exit code: {', '.join(map(str, wrong))} in some runs, "
                f"recorded {self.recorded_exit_code}"
            )
        for output in self.outputs:
            state = "OK" if output.ok else "DIFFERS"
            lines.append(
                f"  [{state}] {safe_text(output.path)} ({output.mode}): "
                f"{safe_text(output.message, 600)}"
            )
            if output.suggestion:
                lines.append(f"           suggestion: {safe_text(output.suggestion, 300)}")
        lines.append("")
        lines.extend(f"  note: {note}" for note in self.notes)
        lines.append("")
        lines.append(f"RESULT: {'STABLE' if self.ok else 'VARIES'}")
        return "\n".join(lines)


def _number(value: object) -> float | None:
    """``value`` as a float if it is a number that converts without loss, else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return float(value) if abs(value) <= _EXACT_INT_LIMIT else None
    if isinstance(value, float):
        return value
    return None


def _round_up(value: float) -> float:
    """``value`` rounded up to one significant digit (``3.2e-07`` -> ``4e-07``)."""
    if value <= 0 or not math.isfinite(value):
        return value
    exponent = math.floor(math.log10(value))
    step = 10.0**exponent
    return math.ceil(value / step) * step


def _spread(samples: list[list[Leaf]], rtol: float, atol: float) -> tuple[float, float, int, str]:
    """``(max_abs, max_rel, outside, first_problem)`` over the numbers of ``samples``.

    ``outside`` counts the locations whose spread is larger than the tolerance rule allows;
    ``first_problem`` describes a difference that is not a number spread (a text, a missing key).
    """
    tables = [dict(sample) for sample in samples]
    max_abs = max_rel = 0.0
    outside = 0
    problem = ""
    for location in sorted({loc for table in tables for loc in table}):
        if any(location not in table for table in tables):
            problem = problem or f"{location} is not present in every run"
            continue
        values = [table[location] for table in tables]
        numbers = [_number(v) for v in values]
        if all(n is not None for n in numbers):
            floats: list[float] = [n for n in numbers if n is not None]
            if any(math.isnan(f) for f in floats):
                if not all(math.isnan(f) for f in floats):
                    problem = problem or f"{location} is NaN in some runs only"
                continue
            if any(math.isinf(f) for f in floats):
                if len(set(floats)) > 1:
                    problem = problem or f"{location} is infinite in some runs only"
                continue
            low, high = min(floats), max(floats)
            spread = high - low
            if spread == 0:
                continue
            scale = max(abs(low), abs(high))
            max_abs = max(max_abs, spread)
            max_rel = max(max_rel, spread / scale)
            if spread > max(rtol * scale, atol):
                outside += 1
        elif any(v != values[0] or type(v) is not type(values[0]) for v in values):
            problem = problem or f"{location} is not the same value in every run"
    return max_abs, max_rel, outside, problem


def _fingerprint(path: Path, directory: bool) -> str | None:
    if directory:
        if not path.is_dir():
            return None
        try:
            return tree_digest(scan_tree(path))
        except TreeError:
            return None
    return sha256_file(path)[0] if path.is_file() else None


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink()


def calibrate(
    manifest_path: Path | str,
    *,
    runs: int = 5,
    assume_yes: bool = False,
    confirm: Callable[[str], bool] | None = None,
    timeout: float | None = None,
) -> CalibrationReport | None:
    """Run the command of ``manifest_path`` ``runs`` times and report how its outputs vary.

    Returns ``None`` if the confirmation was declined (nothing ran). Raises
    ``ConfirmationRequired`` without ``assume_yes`` or a ``confirm`` callback.
    """
    if not MIN_RUNS <= runs <= MAX_RUNS:
        raise UsageError(f"--runs must be between {MIN_RUNS} and {MAX_RUNS}")
    manifest_path = Path(manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    base = manifest_path.parent
    argv = list(manifest["command"]["argv"])
    items = manifest["outputs"]
    paths = [o["path"] for o in items]
    prompt = (
        f"SciProof is about to EXECUTE the command recorded in this manifest {runs} times:\n\n"
        f"    {' '.join(safe_text(part, 200) for part in argv)}\n\n"
        f"    working directory: {base}\n"
        f"    outputs moved aside first and put back afterwards: "
        f"{', '.join(map(safe_text, paths)) or '(none)'}\n\n"
        "It will run with your current user's permissions and can read, change or delete anything "
        "you can, with all of your environment variables. A manifest is untrusted input: only "
        "continue if you trust its source and have read the command above."
    )
    if not assume_yes:
        if confirm is None:
            raise ConfirmationRequired(
                "refusing to execute the recorded command without confirmation; re-run with "
                "--yes after reading the command (see 'sciproof inspect')"
            )
        if not confirm(prompt):
            return None

    targets = {p: resolve_inside(base, p) for p in paths}
    directory = {o["path"] for o in items if o.get("kind") == "directory"}
    exit_codes: list[int] = []
    exact: dict[str, list[str | None]] = {p: [] for p in paths}
    numeric: dict[str, list[list[Leaf] | str]] = {p: [] for p in paths}
    with tempfile.TemporaryDirectory(prefix="sciproof-calibrate-") as scratch:
        backup = Path(scratch)
        moved = _backup_and_remove(base, paths, backup)
        try:
            for _ in range(runs):
                result = run_command(argv, base, timeout)
                exit_codes.append(result.exit_code)
                for item in items:
                    path = item["path"]
                    target = targets[path]
                    if item["compare"]["mode"] == "numeric":
                        try:
                            numeric[path].append(
                                load_numeric(target) if target.is_file() else "absent"
                            )
                        except NumericFormatError as exc:
                            numeric[path].append(f"cannot be read: {exc}")
                    else:
                        exact[path].append(_fingerprint(target, path in directory))
                    _remove(target)
        finally:
            for target in targets.values():
                _remove(target)
            _restore(base, backup, moved)

    report = CalibrationReport(
        name=manifest["name"],
        runs=runs,
        recorded_exit_code=manifest["execution"]["exit_code"],
        exit_codes=exit_codes,
    )
    for item in items:
        report.outputs.append(_judge(base, item, exact[item["path"]], numeric[item["path"]]))
    report.notes.append(
        f"{runs} runs only sample the variation: the suggested rtol is {SAFETY_FACTOR} times the "
        "largest relative spread seen, and a longer or different run can vary more."
    )
    report.notes.append(
        "this measures noise between runs on this machine, not whether the result is right; "
        "the manifest and the outputs were not changed."
    )
    return report


def _judge(
    base: Path, item: dict[str, Any], digests: list[str | None], tables: list[Any]
) -> OutputSpread:
    path = item["path"]
    if item["compare"]["mode"] == "exact":
        distinct = len(set(digests))
        recorded = item.get("sha256")
        if None in digests:
            return OutputSpread(path, "exact", False, "missing after some runs")
        if distinct > 1:
            return OutputSpread(
                path,
                "exact",
                False,
                f"{distinct} different contents in {len(digests)} runs: not deterministic, so an "
                "exact hash cannot be verified; if it is a table of numbers declare it with "
                "--output-numeric, otherwise remove the source of the variation",
            )
        if recorded and digests[0] != recorded:
            return OutputSpread(
                path, "exact", False, "identical in every run, but different from the recorded one"
            )
        return OutputSpread(path, "exact", True, "identical in every run and as recorded")

    rtol, atol = item["compare"]["rtol"], item["compare"]["atol"]
    unreadable = [t for t in tables if isinstance(t, str)]
    if unreadable:
        return OutputSpread(
            path, "numeric", False, f"{unreadable[0]} in some runs", suggestion=None
        )
    samples: list[list[Leaf]] = list(tables)
    snapshot = item["compare"].get("snapshot")
    if snapshot:
        # the recorded copy is checked by verify; the runs can be compared without it
        with contextlib.suppress(NumericFormatError, OSError, ValueError):
            samples.append(load_numeric(resolve_inside(base, snapshot["path"])))
    max_abs, max_rel, outside, problem = _spread(samples, rtol, atol)
    if problem:
        return OutputSpread(path, "numeric", False, problem)
    if max_rel == 0.0:
        return OutputSpread(path, "numeric", True, "identical in every run and as recorded")
    suggestion = (
        f"--tolerance {path} 'rtol={_round_up(max_rel * SAFETY_FACTOR):.0e}'" if outside else None
    )
    detail = f"varies by up to {max_rel:.3g} relative ({max_abs:.3g} absolute)"
    if outside:
        return OutputSpread(
            path,
            "numeric",
            False,
            f"{detail}; {outside} value(s) outside the recorded tolerance "
            f"(rtol={rtol}, atol={atol})",
            max_abs,
            max_rel,
            suggestion,
        )
    return OutputSpread(
        path,
        "numeric",
        True,
        f"{detail}; inside the recorded tolerance (rtol={rtol}, atol={atol})",
        max_abs,
        max_rel,
    )
