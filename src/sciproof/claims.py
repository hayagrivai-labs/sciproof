"""Claims: statements about a value in an output file that can be checked mechanically.

A claim names an output file, a location inside it and a comparison, for example

    out/metrics.json   $.accuracy >= 0.9
    out/metrics.json   $.loss ~= 0.25 atol=0.01
    out/table.csv      r3c1 == 12

The location is exactly the one that appears in difference reports (``$.key[index]`` for JSON,
``rNcM`` for CSV, where row 0 is the first line). Claims are stored in the manifest and are
checked by ``verify`` against the recorded output and, with ``--rerun``, against the output of
the rerun.

A claim that holds shows that *this value satisfies this comparison*. It does not show that the
comparison is the right scientific criterion, that the number means what its name suggests, or
that the experiment is sound. SciProof does not interpret claims beyond the comparison.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from sciproof.errors import UsageError

OPS = ("==", "!=", "<", "<=", ">", ">=", "~=")
ORDER_OPS = ("<", "<=", ">", ">=")
MAX_CLAIMS = 100
MAX_LOCATION_LENGTH = 300
JSON_LOCATION = re.compile(r"^\$(?:\.[^\s\[\]\.#]+|\[\d+\])*$")
CSV_LOCATION = re.compile(r"^r\d+c\d+$")
SUPPORTED_SUFFIXES = (".json", ".csv")
MISSING = object()


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def location_matches_file(location: str, output: str) -> bool:
    """Whether ``location`` is a valid location for the file type of ``output``."""
    if len(location) > MAX_LOCATION_LENGTH:
        return False
    if output.lower().endswith(".json"):
        return bool(JSON_LOCATION.fullmatch(location))
    if output.lower().endswith(".csv"):
        return bool(CSV_LOCATION.fullmatch(location))
    return False


def claim_text(claim: dict[str, Any]) -> str:
    """The claim as people write it, e.g. ``out/m.json: $.acc >= 0.9``."""
    text = f"{claim['output']}: {claim['location']} {claim['op']} {json.dumps(claim['value'])}"
    if claim["op"] == "~=":
        text += f" (rtol={claim['rtol']}, atol={claim['atol']})"
    return text


def claim_expression(claim: dict[str, Any]) -> str:
    """The ``LOCATION OP VALUE [rtol=X atol=Y]`` text that parse_claim() turns back into a claim."""
    text = f"{claim['location']} {claim['op']} {json.dumps(claim['value'])}"
    if claim["op"] == "~=":
        text += f" rtol={claim['rtol']!r} atol={claim['atol']!r}"
    return text


def parse_claim(output: str, expression: str) -> dict[str, Any]:
    """Turn ``output`` and ``"LOCATION OP VALUE [rtol=X] [atol=Y]"`` into a manifest claim.

    The output path is returned as given; the caller makes it relative to the project. Raises
    ``UsageError`` with an explanation of what is wrong.
    """
    parts = expression.split(None, 2)
    if len(parts) != 3:
        raise UsageError(
            f"claim {expression!r}: expected 'LOCATION OP VALUE', for example '$.accuracy >= 0.9'"
        )
    location, op, rest = parts
    if op not in OPS:
        raise UsageError(
            f"claim {expression!r}: unknown comparison {op!r}; use one of {', '.join(OPS)}"
        )
    if not output.lower().endswith(SUPPORTED_SUFFIXES):
        raise UsageError(f"claim on {output!r}: claims support .json and .csv outputs")
    if not location_matches_file(location, output):
        example = "$.key[0].name" if output.lower().endswith(".json") else "r2c1"
        raise UsageError(
            f"claim {expression!r}: {location!r} is not a location in {output!r}; use the form "
            f"{example} (the one shown in difference reports; keys with dots or spaces cannot be "
            "addressed)"
        )
    tolerances: dict[str, float] = {}
    value_text = rest.strip()
    while True:
        match = re.search(r"\s+(rtol|atol)=(\S+)\s*$", value_text)
        if not match:
            break
        name = match.group(1)
        if name in tolerances:
            raise UsageError(f"claim {expression!r}: {name} given twice")
        try:
            number = float(match.group(2))
        except ValueError:
            raise UsageError(f"claim {expression!r}: {name} must be a number") from None
        if not math.isfinite(number) or number < 0:
            raise UsageError(
                f"claim {expression!r}: {name} must be a finite number that is not negative"
            )
        tolerances[name] = number
        value_text = value_text[: match.start()].strip()
    if tolerances and op != "~=":
        raise UsageError(f"claim {expression!r}: rtol and atol only apply to '~='")
    try:
        value = json.loads(value_text, parse_constant=_refuse_constant)
    except (ValueError, RecursionError):
        raise UsageError(
            f"claim {expression!r}: the value must be a JSON number, a string in double quotes, "
            'true, false or null, for example 0.9 or "ok"'
        ) from None
    if isinstance(value, (list, dict)):
        raise UsageError(
            f"claim {expression!r}: the value must be a single number, string, boolean or null"
        )
    if (op in ORDER_OPS or op == "~=") and not is_number(value):
        raise UsageError(f"claim {expression!r}: {op} needs a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise UsageError(f"claim {expression!r}: the value must be finite")
    claim: dict[str, Any] = {"output": output, "location": location, "op": op, "value": value}
    if op == "~=":
        claim["rtol"] = tolerances.get("rtol", 0.0)
        claim["atol"] = tolerances.get("atol", 0.0)
    return claim


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed")


@dataclass(frozen=True)
class ClaimResult:
    holds: bool
    found: Any  # the value in the file (JSON-safe), or ``MISSING``
    message: str


def json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    return value


def evaluate(claim: dict[str, Any], leaves: dict[str, object]) -> ClaimResult:
    """Check one claim against the ``{location: value}`` leaves of an output file."""
    location, op, expected = claim["location"], claim["op"], claim["value"]
    if location not in leaves:
        return ClaimResult(False, MISSING, f"{location} is not present in {claim['output']}")
    found = leaves[location]
    shown = json.dumps(json_safe(found)) if not isinstance(found, str) else json.dumps(found[:200])
    if op in ORDER_OPS or op == "~=":
        if not is_number(found):
            return ClaimResult(False, found, f"{location} is {shown}, which is not a number")
        if isinstance(found, float) and math.isnan(found):
            return ClaimResult(False, found, f"{location} is NaN, which satisfies no comparison")
    holds = _holds(op, found, expected, claim)
    verb = "holds" if holds else "does not hold"
    return ClaimResult(
        holds, found, f"{verb}: {location} is {shown}, claim is {_claim_expression(claim)}"
    )


def _claim_expression(claim: dict[str, Any]) -> str:
    text = f"{claim['op']} {json.dumps(claim['value'])}"
    if claim["op"] == "~=":
        text += f" (rtol={claim['rtol']}, atol={claim['atol']})"
    return text


def _holds(op: str, found: Any, expected: Any, claim: dict[str, Any]) -> bool:
    if op == "==":
        return _equal(found, expected)
    if op == "!=":
        return not _equal(found, expected)
    if op == "~=":
        a, b = float(found), float(expected)
        if math.isinf(a) or math.isinf(b):
            return a == b
        return abs(a - b) <= max(claim["rtol"] * max(abs(a), abs(b)), claim["atol"])
    if op == "<":
        return bool(found < expected)
    if op == "<=":
        return bool(found <= expected)
    if op == ">":
        return bool(found > expected)
    return bool(found >= expected)


def _equal(found: Any, expected: Any) -> bool:
    if is_number(found) and is_number(expected):
        return bool(found == expected)
    return type(found) is type(expected) and found == expected
