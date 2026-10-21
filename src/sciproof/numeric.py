"""Numerical comparison of JSON, CSV and TSV files with explicit tolerances.

A value is compared as a number only if the file format says it is one (JSON numbers) or
the whole CSV cell parses as a float. Everything else (strings, booleans, ``null``) must be
equal exactly. Two NaNs compare equal; infinities must match in sign.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sciproof.errors import SciProofError
from sciproof.jsonstrict import reject_duplicate_keys

MAX_NUMERIC_FILE_BYTES = 64 * 1024 * 1024
MAX_REPORTED_DIFFERENCES = 10
SUPPORTED_SUFFIXES = (".json", ".csv", ".tsv", ".npy")
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
    """Parse a ``.json``, ``.csv``, ``.tsv`` or ``.npy`` file into ``(location, value)`` pairs."""
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
            _flatten_json(json.loads(text, object_pairs_hook=reject_duplicate_keys), "$", leaves)
        except (ValueError, RecursionError) as exc:
            raise NumericFormatError(f"{path.name} is not valid JSON: {exc}") from exc
        return leaves

    delimiter = "\t" if suffix == ".tsv" else ","
    try:
        # newline="" lets the csv module handle line breaks inside quoted fields (and CRLF)
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error as exc:
        format_name = "TSV" if suffix == ".tsv" else "CSV"
        raise NumericFormatError(f"{path.name} is not valid {format_name}: {exc}") from exc
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


@dataclass(frozen=True)
class Difference:
    """One difference between two files, with the numbers behind it when there are numbers.

    ``message`` is the human-readable line that ``compare`` returns. The other fields are what a
    table or a script needs: ``location``, the ``recorded`` and ``found`` values (JSON-safe),
    and, for two numbers, the absolute and relative difference and the largest absolute
    difference the tolerance would have allowed.
    """

    message: str
    kind: str = "value"  # "number", "value", "structure" or "summary"
    location: str | None = None
    recorded: Any = None
    found: Any = None
    abs_diff: float | None = None
    rel_diff: float | None = None
    allowed: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "location": self.location,
            "recorded": self.recorded,
            "found": self.found,
            "abs_diff": self.abs_diff,
            "rel_diff": self.rel_diff,
            "allowed": self.allowed,
            "message": self.message,
        }


def _jsonable(value: Any) -> Any:
    """A value as strict JSON can carry it: no NaN/Infinity literals, nothing huge."""
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()  # a NumPy scalar
        except (ValueError, AttributeError):
            return _short(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, int):
        return value if len(str(value)) <= 30 else _short(value)
    if isinstance(value, str):
        return value if len(value) <= 200 else f"{value[:200]}... ({len(value)} characters)"
    return _short(value)


def _number_details(
    a: float, b: float, rtol: float, atol: float
) -> tuple[float | None, float | None, float | None]:
    """``(abs_diff, rel_diff, allowed)`` for two numbers; ``None`` where they are undefined."""
    if not (math.isfinite(a) and math.isfinite(b)):
        return None, None, None
    biggest = max(abs(a), abs(b))
    absolute = abs(a - b)
    allowed = max(rtol * biggest, atol)
    if not (math.isfinite(absolute) and math.isfinite(allowed)):
        return None, None, None
    if biggest > 0:
        relative: float | None = absolute / biggest
    else:
        relative = 0.0 if absolute == 0 else None
    return absolute, relative, allowed


def _compare_arrays(expected: Any, actual: Any, rtol: float, atol: float) -> list[Difference]:
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
    found: list[Difference] = []
    for index in np.argwhere(bad)[:MAX_REPORTED_DIFFERENCES]:
        key = tuple(int(i) for i in index)
        location = f"data{list(key)}"
        recorded, seen = expected[key], actual[key]
        absolute = relative = allowed = None
        if expected.dtype.kind in "iuf" and actual.dtype.kind in "iuf":
            absolute, relative, allowed = _number_details(float(recorded), float(seen), rtol, atol)
        found.append(
            Difference(
                f"{location}: recorded {recorded!r}, found {seen!r} (rtol={rtol}, atol={atol})",
                "number" if absolute is not None else "value",
                location,
                _jsonable(recorded),
                _jsonable(seen),
                absolute,
                relative,
                allowed,
            )
        )
    if count > len(found):
        found.append(Difference(f"... {count} of {bad.size} values differ", "summary"))
    return found


def _is_exact_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _short(value: object, limit: int = 60) -> str:
    """repr() of a value, shortened so that a huge integer cannot flood the report."""
    text = repr(value)
    return text if len(text) <= limit else f"{text[:limit]}... ({len(text)} characters)"


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numbers_close(a: float, b: float, rtol: float, atol: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= max(rtol * max(abs(a), abs(b)), atol)


_CSV_CELL = re.compile(r"r(\d+)c(\d+)")


def _csv_headers(leaves: list[Leaf]) -> dict[int, str]:
    """Column index -> header text, taken from the first CSV/TSV row (empty for other formats)."""
    headers: dict[int, str] = {}
    for location, value in leaves:
        match = _CSV_CELL.fullmatch(location)
        if match and match.group(1) == "0" and isinstance(value, str):
            headers[int(match.group(2))] = value
    return headers


def _describe(location: str, headers: dict[int, str]) -> str:
    """Add the CSV/TSV column name to a cell location, e.g. ``r2c1 (column 'loss')``."""
    match = _CSV_CELL.fullmatch(location)
    if match and match.group(1) != "0" and int(match.group(2)) in headers:
        return f"{location} (column {_short(headers[int(match.group(2))], 40)})"
    return location


def compare_detailed(
    expected: list[Leaf], actual: list[Leaf], rtol: float, atol: float
) -> list[Difference]:
    """Like ``compare`` but returns ``Difference`` records (at most 10)."""
    differences: list[Difference] = []
    headers = _csv_headers(expected)

    def note(difference: Difference) -> bool:
        differences.append(difference)
        return len(differences) >= MAX_REPORTED_DIFFERENCES

    if len(expected) != len(actual):
        note(
            Difference(
                f"structure differs: {len(expected)} values recorded, {len(actual)} found",
                "structure",
            )
        )
    for (loc_e, val_e), (loc_a, val_a) in zip(expected, actual, strict=False):
        if loc_e != loc_a:
            note(
                Difference(
                    f"structure differs at {loc_e}: found {loc_a} instead", "structure", loc_e
                )
            )
            break
        if loc_e == "data" and hasattr(val_e, "shape"):
            if (
                expected[0][1] == actual[0][1] and expected[1][1] == actual[1][1]
            ):  # same shape and dtype
                differences.extend(_compare_arrays(val_e, val_a, rtol, atol))
            continue
        absolute = relative = allowed = None
        kind = "value"
        if _is_exact_int(val_e) and _is_exact_int(val_a):
            # Python ints are exact; going through float would hide differences above 2**53.
            different = val_e != val_a
            detail = ""
            if different:
                kind = "number"
                with contextlib.suppress(OverflowError):
                    absolute, relative, _ = _number_details(float(val_e), float(val_a), 0.0, 0.0)
                allowed = 0.0 if absolute is not None else None  # integers are compared exactly
        elif _is_number(val_e) and _is_number(val_a):
            kind = "number"
            try:
                a, b = float(val_e), float(val_a)  # type: ignore[arg-type]
                different = not _numbers_close(a, b, rtol, atol)
                absolute, relative, allowed = _number_details(a, b, rtol, atol)
            except OverflowError:  # an integer beyond the float range cannot match a float
                different = True
            detail = f" (rtol={rtol}, atol={atol})"
        else:
            # True == 1 in Python, but a boolean is not a number in the data
            different = type(val_e) is not type(val_a) or val_e != val_a
            detail = ""
        if different:
            location = _describe(loc_e, headers)
            record = Difference(
                f"{location}: recorded {_short(val_e)}, found {_short(val_a)}{detail}",
                kind,
                location,
                _jsonable(val_e),
                _jsonable(val_a),
                absolute,
                relative,
                allowed,
            )
            if note(record):
                break
    return differences


def compare(expected: list[Leaf], actual: list[Leaf], rtol: float, atol: float) -> list[str]:
    """Return human-readable differences (empty list means equivalent within tolerance).

    Locations are ``$.key[index]`` for JSON, ``rNcM`` (row, column; row 0 is the first line,
    usually the header) for CSV/TSV, and ``data[i, j]`` for arrays.
    """
    return [d.message for d in compare_detailed(expected, actual, rtol, atol)]
