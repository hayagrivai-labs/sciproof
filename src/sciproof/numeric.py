"""Numerical comparison of JSON and CSV files with explicit tolerances.

A value is compared as a number only if the file format says it is one (JSON numbers) or
the whole CSV cell parses as a float. Everything else (strings, booleans, ``null``) must be
equal exactly. Two NaNs compare equal; infinities must match in sign.
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any

from sciproof.errors import SciProofError

MAX_NUMERIC_FILE_BYTES = 64 * 1024 * 1024
MAX_REPORTED_DIFFERENCES = 10
SUPPORTED_SUFFIXES = (".json", ".csv", ".npy")
NUMPY_KINDS = "biufc"  # bool, int, unsigned, float, complex

Leaf = tuple[str, object]


class NumericFormatError(SciProofError):
    """The file cannot be compared numerically."""


def _flatten_json(value: object, path: str, out: list[Leaf]) -> None:
    if isinstance(value, dict):
        out.append((f"{path}#keys", sorted(value)))
        for key in sorted(value):
            _flatten_json(value[key], f"{path}.{key}", out)
    elif isinstance(value, list):
        out.append((f"{path}#len", len(value)))
        for index, item in enumerate(value):
            _flatten_json(item, f"{path}[{index}]", out)
    else:
        out.append((path, value))


def load_numeric(path: Path) -> list[Leaf]:
    """Parse a ``.json``, ``.csv`` or ``.npy`` file into ``(location, value)`` pairs."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise NumericFormatError(
            f"numeric comparison supports {', '.join(SUPPORTED_SUFFIXES)} files, got {path.name!r}"
        )
    try:
        size = path.stat().st_size
        if size > MAX_NUMERIC_FILE_BYTES:
            raise NumericFormatError(f"{path.name} is larger than the numeric comparison limit")
    except OSError as exc:
        raise NumericFormatError(f"cannot read {path.name}: {exc}") from exc
    if suffix == ".npy":
        return _load_npy(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise NumericFormatError(f"cannot read {path.name} as UTF-8 text: {exc}") from exc

    leaves: list[Leaf] = []
    if suffix == ".json":
        try:
            _flatten_json(json.loads(text), "$", leaves)
        except (ValueError, RecursionError) as exc:
            raise NumericFormatError(f"{path.name} is not valid JSON: {exc}") from exc
        return leaves

    try:
        # newline="" lets the csv module handle line breaks inside quoted fields (and CRLF)
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise NumericFormatError(f"{path.name} is not valid CSV: {exc}") from exc
    leaves.append(("rows", len(rows)))
    for r, row in enumerate(rows):
        leaves.append((f"row{r}#cols", len(row)))
        for c, cell in enumerate(row):
            leaves.append((f"r{r}c{c}", _parse_cell(cell)))
    return leaves


def _parse_cell(cell: str) -> object:
    """A CSV cell is a number if float() accepts it; ``1_000`` (digit separators) is text."""
    if "_" in cell:
        return cell
    try:
        return float(cell)
    except ValueError:
        return cell


def _load_npy(path: Path) -> list[Leaf]:
    """Load a NumPy ``.npy`` file (needs the optional NumPy extra; pickle is never allowed)."""
    try:
        import numpy as np
    except ImportError as exc:
        raise NumericFormatError(
            f"comparing {path.name} needs NumPy: pip install 'sciproof[numpy]'"
        ) from exc
    try:
        array = np.load(path, allow_pickle=False)
    except (ValueError, OSError, EOFError) as exc:
        raise NumericFormatError(f"{path.name} is not a plain .npy array: {exc}") from exc
    if not isinstance(array, np.ndarray) or array.dtype.kind not in NUMPY_KINDS:
        raise NumericFormatError(
            f"{path.name}: only numeric, boolean and complex arrays are supported "
            f"(dtype {getattr(array, 'dtype', type(array).__name__)})"
        )
    return [("shape", list(array.shape)), ("dtype", str(array.dtype)), ("data", array)]


def _compare_arrays(expected: Any, actual: Any, rtol: float, atol: float) -> list[str]:
    """Element-wise comparison of two equally shaped arrays with the scalar rule of ``compare``."""
    import numpy as np

    if expected.dtype.kind in "biu" and actual.dtype.kind in "biu":
        bad = expected != actual  # integers and booleans are compared exactly
    else:
        with np.errstate(all="ignore"):
            both_nan = np.isnan(expected) & np.isnan(actual)
            same_inf = np.isinf(expected) & (expected == actual)
            finite = np.isfinite(expected) & np.isfinite(actual)
            tolerance = np.maximum(rtol * np.maximum(np.abs(expected), np.abs(actual)), atol)
            close = finite & (np.abs(expected - actual) <= tolerance)
        bad = ~(both_nan | same_inf | close)
    count = int(bad.sum())
    if not count:
        return []
    messages = []
    for index in np.argwhere(bad)[:MAX_REPORTED_DIFFERENCES]:
        key = tuple(int(i) for i in index)
        messages.append(
            f"data{list(key)}: recorded {expected[key]!r}, found {actual[key]!r} "
            f"(rtol={rtol}, atol={atol})"
        )
    if count > len(messages):
        messages.append(f"... {count} of {bad.size} values differ")
    return messages


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numbers_close(a: float, b: float, rtol: float, atol: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= max(rtol * max(abs(a), abs(b)), atol)


def compare(expected: list[Leaf], actual: list[Leaf], rtol: float, atol: float) -> list[str]:
    """Return human-readable differences (empty list means equivalent within tolerance)."""
    differences: list[str] = []

    def note(message: str) -> bool:
        differences.append(message)
        return len(differences) >= MAX_REPORTED_DIFFERENCES

    if len(expected) != len(actual):
        note(f"structure differs: {len(expected)} values recorded, {len(actual)} found")
    for (loc_e, val_e), (loc_a, val_a) in zip(expected, actual, strict=False):
        if loc_e != loc_a:
            note(f"structure differs at {loc_e}: found {loc_a} instead")
            break
        if loc_e == "data" and hasattr(val_e, "shape"):
            if (
                expected[0][1] == actual[0][1] and expected[1][1] == actual[1][1]
            ):  # same shape and dtype
                differences.extend(_compare_arrays(val_e, val_a, rtol, atol))
            continue
        if isinstance(val_e, int) and isinstance(val_a, int) and not isinstance(val_e, bool):
            # Python ints are exact; going through float would hide differences above 2**53.
            different = val_e != val_a
            detail = ""
        elif _is_number(val_e) and _is_number(val_a):
            different = not _numbers_close(float(val_e), float(val_a), rtol, atol)  # type: ignore[arg-type]
            detail = f" (rtol={rtol}, atol={atol})"
        else:
            # True == 1 in Python, but a boolean is not a number in the data
            different = type(val_e) is not type(val_a) or val_e != val_a
            detail = ""
        if different and note(f"{loc_e}: recorded {val_e!r}, found {val_a!r}{detail}"):
            break
    return differences
