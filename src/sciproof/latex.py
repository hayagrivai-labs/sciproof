"""Numbers for a paper, taken from verified outputs: ``sciproof export --format latex``.

A number typed into a paper by hand can drift away from the experiment that produced it. This
module writes the values of chosen locations of the declared outputs as LaTeX macros::

    % SciProof 0.3.2: values of baseline.sciproof.json (verification: PASS)
    \\newcommand{\\accuracy}{0.93}

so that the paper says ``\\accuracy{}`` and the number can only come from the recorded output.

Nothing is written unless the integrity verification of the manifest passes: the file is read
only after its SHA-256 has been checked, and the named locations must exist in a declared output
of a supported type (``.json``, ``.csv``, ``.tsv``). Nothing is executed. A passing verification
shows that the files are what was recorded; it does not show that the number is correct.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from sciproof import __version__
from sciproof.claims import location_matches_file
from sciproof.errors import SciProofError, UsageError
from sciproof.manifest import load_manifest
from sciproof.numeric import NumericFormatError, load_numeric
from sciproof.paths import resolve_inside, validate_relative_path
from sciproof.textutil import safe_text
from sciproof.verify import Status, verify

MACRO_NAME = re.compile(r"[A-Za-z]{1,40}")
MAX_VALUES = 200
_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
# TeX does not allow these in a macro body without trouble, and they do not belong in a number
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _text(value: str) -> str:
    cleaned = _CONTROL.sub(" ", value)
    return "".join(_LATEX_SPECIAL.get(char, char) for char in cleaned)


def format_value(value: object) -> str:
    """``value`` as the body of a LaTeX macro."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return r"$\infty$" if value > 0 else r"$-\infty$"
        if value != 0 and not 1e-4 <= abs(value) < 1e6:
            mantissa, _, exponent = f"{value:.9e}".partition("e")
            mantissa = mantissa.rstrip("0").rstrip(".")
            return rf"${mantissa}\times 10^{{{int(exponent)}}}$"
        return f"{value:.10g}"
    return _text(str(value))


def _read(base: Path, output: str, location: str, cache: dict[str, dict[str, object]]) -> object:
    if output not in cache:
        try:
            cache[output] = dict(load_numeric(resolve_inside(base, output)))
        except NumericFormatError as exc:
            raise SciProofError(f"{output}: {exc}") from exc
    leaves = cache[output]
    if location not in leaves:
        raise SciProofError(f"{location} is not present in {output}")
    return leaves[location]


def to_latex(manifest_path: Path | str, values: list[tuple[str, str, str]]) -> str:
    """``\\newcommand`` lines for ``values`` (``(macro name, output file, location)``).

    Raises ``SciProofError`` unless the integrity verification of the manifest is ``PASS`` and every
    value can be read. Nothing is executed.
    """
    manifest_path = Path(manifest_path).resolve()
    if not values:
        raise UsageError("give at least one --value NAME OUTPUT LOCATION")
    if len(values) > MAX_VALUES:
        raise UsageError(f"at most {MAX_VALUES} values per export")
    names = [name for name, _, _ in values]
    for name in names:
        if not MACRO_NAME.fullmatch(name):
            raise UsageError(
                f"--value {safe_text(name, 60)!r}: a LaTeX macro name is 1 to 40 letters "
                "(no digits, underscores or punctuation)"
            )
    if len(set(names)) != len(names):
        raise UsageError("a macro name is used more than once")

    manifest = load_manifest(manifest_path)
    base = manifest_path.parent
    declared = {o["path"]: o for o in manifest["outputs"]}
    for _, output, location in values:
        validate_relative_path(output)
        item = declared.get(output)
        if item is None or item.get("kind") == "directory" or not item.get("present", True):
            raise UsageError(f"{output!r} is not a declared output file of this manifest")
        if not location_matches_file(location, output):
            raise UsageError(
                f"{location!r} is not a location in {output!r}; use $.key[0].name for JSON or "
                "r2c1 for CSV/TSV (the form shown in difference reports)"
            )

    report = verify(manifest_path)
    if report.status is not Status.PASS:
        raise SciProofError(
            f"not exporting values: the verification of {manifest_path.name} is "
            f"{report.status.value} ({report.reason}). Run 'sciproof verify' to see why."
        )

    cache: dict[str, dict[str, object]] = {}
    lines = [
        f"% SciProof {__version__}: values of {_text_comment(manifest_path.name)} "
        f"(integrity verification: PASS)",
        "% Generated by 'sciproof export --format latex'; do not edit by hand.",
    ]
    for name, output, location in values:
        value: Any = _read(base, output, location, cache)
        lines.append(f"\\newcommand{{\\{name}}}{{{format_value(value)}}}")
    return "\n".join(lines) + "\n"


def _text_comment(text: str) -> str:
    """A file name for a ``%`` comment line: no line break may end the comment early."""
    return _CONTROL.sub(" ", text)
