"""Compare two evidence manifests: what changed between two recorded runs?

The question this answers is "my result moved; did the command, the inputs or the environment
move with it?". Nothing is executed. Both manifests are validated like any other manifest, and
only files that the manifests themselves reference (the snapshots of numeric outputs) are read.

A difference in an input, the command or the environment is a *possible explanation* of a
changed output, not proof of the cause.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sciproof.claims import claim_text
from sciproof.environment import normalize_package_name
from sciproof.errors import UnsafePathError
from sciproof.hashing import sha256_file
from sciproof.manifest import load_manifest
from sciproof.markdown import escape
from sciproof.numeric import NumericFormatError, compare_detailed, load_numeric
from sciproof.paths import resolve_inside
from sciproof.textutil import safe_text
from sciproof.treehash import diff_trees

DIFF_SCHEMA = "sciproof.diff"
DIFF_SCHEMA_VERSION = "1.0"
SECTIONS = ("command", "note", "environment", "git", "inputs", "outputs", "claims", "execution")
SYMBOL = {"added": "+", "removed": "-", "changed": "~"}
MAX_LINES_PER_SECTION = 20

NOTE = (
    "a difference in the command, an input or the environment is a possible explanation of a "
    "changed output, not proof of the cause; nothing was executed"
)


@dataclass
class Change:
    section: str
    kind: str  # "added", "removed" or "changed"
    subject: str
    message: str
    evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "kind": self.kind,
            "subject": self.subject,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclass
class DiffReport:
    old_name: str
    new_name: str
    old_file: str
    new_file: str
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(
        self,
        section: str,
        kind: str,
        subject: str,
        message: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.changes.append(Change(section, kind, subject, message, evidence))

    @property
    def identical(self) -> bool:
        return not self.changes

    @property
    def counts(self) -> dict[str, int]:
        return {s: sum(1 for c in self.changes if c.section == s) for s in SECTIONS}

    @property
    def summary(self) -> str:
        if self.identical:
            return "no differences in command, environment, git state, inputs, outputs or exit code"
        parts = ", ".join(f"{s} {n}" for s, n in self.counts.items() if n)
        return f"{len(self.changes)} differences ({parts})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DIFF_SCHEMA,
            "schema_version": DIFF_SCHEMA_VERSION,
            "old": {"name": self.old_name, "file": self.old_file},
            "new": {"name": self.new_name, "file": self.new_file},
            "identical": self.identical,
            "summary": self.summary,
            "counts": self.counts,
            "changes": [c.to_dict() for c in self.changes],
            "notes": [NOTE, *self.notes],
        }

    def to_text(self) -> str:
        lines = [f"SciProof diff: '{safe_text(self.old_name)}' -> '{safe_text(self.new_name)}'", ""]
        for section in SECTIONS:
            items = [c for c in self.changes if c.section == section]
            if not items:
                continue
            lines.append(f"  {section} ({len(items)})")
            for change in items[:MAX_LINES_PER_SECTION]:
                lines.append(
                    f"    {SYMBOL[change.kind]} {safe_text(change.subject)}: "
                    f"{safe_text(change.message, 600)}"
                )
            if len(items) > MAX_LINES_PER_SECTION:
                lines.append(f"    ... and {len(items) - MAX_LINES_PER_SECTION} more")
        if self.changes:
            lines.append("")
        lines.append(f"summary: {self.summary}")
        lines.extend(f"note: {safe_text(n, 600)}" for n in [NOTE, *self.notes])
        lines += ["", "RESULT: IDENTICAL" if self.identical else "RESULT: DIFFERENT"]
        return "\n".join(lines)

    def to_markdown(self) -> str:
        head = "IDENTICAL" if self.identical else "DIFFERENT"
        lines = [
            f"## SciProof diff: {head}: "
            f"{escape(self.old_name, 100)} -> {escape(self.new_name, 100)}",
            "",
            f"> {escape(self.summary, 500)}",
        ]
        for section in SECTIONS:
            items = [c for c in self.changes if c.section == section]
            if not items:
                continue
            lines += [
                "",
                f"### {section} ({len(items)})",
                "",
                "| | Subject | Details |",
                "| --- | --- | --- |",
            ]
            for change in items[:MAX_LINES_PER_SECTION]:
                lines.append(
                    f"| {SYMBOL[change.kind]} | {escape(change.subject)} "
                    f"| {escape(change.message)} |"
                )
            if len(items) > MAX_LINES_PER_SECTION:
                lines.append(f"| | | ... and {len(items) - MAX_LINES_PER_SECTION} more |")
        lines += ["", "### Notes", ""]
        lines.extend(f"- {escape(n, 1000)}" for n in [NOTE, *self.notes])
        lines.append("")
        return "\n".join(lines)


def format_diff_json(report: DiffReport) -> str:
    return json.dumps(report.to_dict(), indent=2, allow_nan=False)


# --------------------------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------------------------


def _text(value: Any, limit: int = 160) -> str:
    return safe_text(str(value), limit)


def _versions(old: Any, new: Any) -> str:
    return f"{_text(old)} -> {_text(new)}"


def _command_line(manifest: dict[str, Any]) -> str:
    return " ".join(manifest["command"]["argv"])


def _tree_state(dirty: bool) -> str:
    return "uncommitted changes" if dirty else "clean"


def _packages(manifest: dict[str, Any]) -> dict[str, tuple[str, str]]:
    return {
        normalize_package_name(d["name"]): (d["name"], d["version"])
        for d in manifest["dependencies"]
    }


def _how_compared(compare: dict[str, Any]) -> str:
    if compare["mode"] == "numeric":
        return f"numeric (rtol={compare['rtol']}, atol={compare['atol']})"
    return "exact"


def _diff_command(old: dict[str, Any], new: dict[str, Any], report: DiffReport) -> None:
    if old["command"]["argv"] != new["command"]["argv"]:
        report.add("command", "changed", "argv", _versions(_command_line(old), _command_line(new)))


def _diff_note(old: dict[str, Any], new: dict[str, Any], report: DiffReport) -> None:
    a, b = old.get("note"), new.get("note")
    if a == b:
        return
    if a is None:
        report.add("note", "added", "note", _text(b, 300))
    elif b is None:
        report.add("note", "removed", "note", f"was {_text(a, 300)!r}")
    else:
        report.add("note", "changed", "note", _versions(_text(a, 150), _text(b, 150)))


def _diff_hardware(old: dict[str, Any], new: dict[str, Any], report: DiffReport) -> None:
    a, b = old["environment"].get("hardware"), new["environment"].get("hardware")
    if a is None and b is None:
        return
    if a is None or b is None:
        report.notes.append("hardware was recorded in one manifest only, so it was not compared")
        return
    for key, label in (("cpu_count", "CPU count"), ("processor", "processor")):
        if a[key] != b[key]:
            report.add("environment", "changed", label, _versions(a[key], b[key]))
    gpus = lambda h: ", ".join(f"{g['name']} ({g['driver']})" for g in h["gpus"]) or "none"  # noqa: E731
    if gpus(a) != gpus(b):
        report.add("environment", "changed", "GPUs", _versions(gpus(a), gpus(b)))


def _diff_environment(old: dict[str, Any], new: dict[str, Any], report: DiffReport) -> None:
    a, b = old["environment"], new["environment"]
    for key in ("version", "implementation"):
        if a["python"].get(key) != b["python"].get(key):
            report.add(
                "environment",
                "changed",
                f"python {key}",
                _versions(a["python"].get(key), b["python"].get(key)),
            )
    for key in ("system", "machine", "release"):
        if a["platform"].get(key) != b["platform"].get(key):
            report.add(
                "environment",
                "changed",
                f"platform {key}",
                _versions(a["platform"].get(key), b["platform"].get(key)),
            )
    for name in sorted(set(a["env_vars"]) | set(b["env_vars"])):
        if name not in b["env_vars"]:
            report.add(
                "environment", "removed", f"variable {name}", f"was {_text(a['env_vars'][name])!r}"
            )
        elif name not in a["env_vars"]:
            report.add(
                "environment", "added", f"variable {name}", f"is {_text(b['env_vars'][name])!r}"
            )
        elif a["env_vars"][name] != b["env_vars"][name]:
            report.add(
                "environment",
                "changed",
                f"variable {name}",
                _versions(a["env_vars"][name], b["env_vars"][name]),
            )
    _diff_hardware(old, new, report)
    if a.get("source") != b.get("source"):
        report.notes.append(
            "the two manifests describe the environment differently "
            f"({_text(a.get('source'))} vs {_text(b.get('source'))}), so package differences may "
            "not be comparable"
        )
    pa, pb = _packages(old), _packages(new)
    for key in sorted(set(pa) | set(pb)):
        if key not in pb:
            report.add("environment", "removed", f"package {pa[key][0]}", f"was {pa[key][1]}")
        elif key not in pa:
            report.add("environment", "added", f"package {pb[key][0]}", f"is {pb[key][1]}")
        elif pa[key][1] != pb[key][1]:
            report.add(
                "environment", "changed", f"package {pb[key][0]}", _versions(pa[key][1], pb[key][1])
            )


def _diff_git(old: dict[str, Any], new: dict[str, Any], report: DiffReport) -> None:
    a, b = old.get("git"), new.get("git")
    if a is None and b is None:
        return
    if a is None or b is None:
        report.add("git", "changed", "repository", "recorded in one manifest only")
        return
    if a["commit"] != b["commit"]:
        report.add("git", "changed", "commit", _versions(a["commit"][:12], b["commit"][:12]))
    if a["dirty"] != b["dirty"]:
        report.add(
            "git",
            "changed",
            "working tree",
            _versions(_tree_state(a["dirty"]), _tree_state(b["dirty"])),
        )


def _snapshot(base: Path, item: dict[str, Any]) -> tuple[Path | None, str | None]:
    """The verified snapshot of a numeric output, or ``(None, reason)``."""
    snapshot = item["compare"]["snapshot"]
    try:
        path = resolve_inside(base, snapshot["path"])
    except UnsafePathError as exc:
        return None, f"unsafe snapshot path ({exc})"
    if not path.is_file():
        return None, f"snapshot {snapshot['path']} is missing"
    if sha256_file(path)[0] != snapshot["sha256"]:
        return None, f"snapshot {snapshot['path']} was modified"
    return path, None


def _numeric_change(
    old_item: dict[str, Any], new_item: dict[str, Any], old_base: Path, new_base: Path
) -> tuple[str, dict[str, Any]]:
    """Compare two numeric outputs with the *baseline* (old) tolerance."""
    compare = old_item["compare"]
    rtol, atol = compare["rtol"], compare["atol"]
    evidence: dict[str, Any] = {"baseline_rtol": rtol, "baseline_atol": atol}
    old_path, why_old = _snapshot(old_base, old_item)
    new_path, why_new = _snapshot(new_base, new_item)
    if old_path is None or new_path is None:
        reason = why_old or why_new or "snapshot unavailable"
        evidence["numeric"] = {"comparable": False, "reason": reason}
        return f"numeric comparison not possible: {reason}", evidence
    try:
        details = compare_detailed(load_numeric(old_path), load_numeric(new_path), rtol, atol)
    except NumericFormatError as exc:
        evidence["numeric"] = {"comparable": False, "reason": str(exc)}
        return f"numeric comparison not possible: {exc}", evidence
    evidence["numeric"] = {
        "comparable": True,
        "equivalent": not details,
        "difference_details": [d.to_dict() for d in details],
    }
    if not details:
        return (
            f"numerically equivalent within the baseline tolerance (rtol={rtol}, atol={atol})",
            evidence,
        )
    return (
        f"outside the baseline tolerance (rtol={rtol}, atol={atol}): {details[0].message}"
        + (f" (and {len(details) - 1} more)" if len(details) > 1 else ""),
        evidence,
    )


def _diff_artifacts(
    section: str,
    old: dict[str, Any],
    new: dict[str, Any],
    old_base: Path,
    new_base: Path,
    report: DiffReport,
) -> None:
    a = {item["path"]: item for item in old[section]}
    b = {item["path"]: item for item in new[section]}
    for path in sorted(set(a) | set(b)):
        if path not in b:
            report.add(section, "removed", path, "declared in the old manifest only")
            continue
        if path not in a:
            report.add(section, "added", path, "declared in the new manifest only")
            continue
        x, y = a[path], b[path]
        kinds = (x.get("kind", "file"), y.get("kind", "file"))
        if kinds[0] != kinds[1]:
            report.add(section, "changed", path, f"was a {kinds[0]}, is now a {kinds[1]}")
            continue
        parts: list[str] = []
        evidence: dict[str, Any] = {"old_sha256": x.get("sha256"), "new_sha256": y.get("sha256")}
        if section == "outputs" and x["present"] != y["present"]:
            parts.append(
                "was present, is now absent" if x["present"] else "was absent, is now present"
            )
        elif x.get("sha256") != y.get("sha256"):
            if kinds[0] == "directory":
                diff = diff_trees(x.get("files") or [], y.get("files") or [])
                parts.append(f"tree changed: {diff.describe()}")
                evidence["tree"] = diff.to_dict()
            else:
                parts.append(
                    f"content changed (sha256 {str(x['sha256'])[:12]} -> {str(y['sha256'])[:12]}, "
                    f"{x['size']} -> {y['size']} bytes)"
                )
        if (
            section == "outputs"
            and x["compare"] != y["compare"]
            and not (
                x["compare"]["mode"] == y["compare"]["mode"] == "numeric"
                and {k: v for k, v in x["compare"].items() if k != "snapshot"}
                == {k: v for k, v in y["compare"].items() if k != "snapshot"}
            )
        ):
            parts.append(
                "comparison changed: "
                f"{_how_compared(x['compare'])} -> {_how_compared(y['compare'])}"
            )
        if (
            section == "outputs"
            and x.get("sha256") != y.get("sha256")
            and x["present"]
            and y["present"]
            and x["compare"]["mode"] == y["compare"]["mode"] == "numeric"
        ):
            text, numeric_evidence = _numeric_change(x, y, old_base, new_base)
            parts.append(text)
            evidence.update(numeric_evidence)
        if parts:
            report.add(section, "changed", path, "; ".join(parts), evidence)


def _diff_claims(old: dict[str, Any], new: dict[str, Any], report: DiffReport) -> None:
    """Claims are identified by what they say; a changed number is a removed and an added claim."""
    a = {claim_text(c): c for c in old.get("claims") or []}
    b = {claim_text(c): c for c in new.get("claims") or []}
    for text in sorted(set(a) - set(b)):
        report.add("claims", "removed", text, "in the old manifest only")
    for text in sorted(set(b) - set(a)):
        report.add("claims", "added", text, "in the new manifest only")


def _diff_execution(old: dict[str, Any], new: dict[str, Any], report: DiffReport) -> None:
    a, b = old["execution"], new["execution"]
    if a["exit_code"] != b["exit_code"]:
        report.add("execution", "changed", "exit code", _versions(a["exit_code"], b["exit_code"]))
    for stream in ("stdout", "stderr"):
        if a[stream]["sha256"] != b[stream]["sha256"]:
            report.add("execution", "changed", stream, "output differs (informational)")


def diff_manifests(old_path: Path | str, new_path: Path | str) -> DiffReport:
    """Compare two manifests. Raises ``ManifestError`` if either one is invalid."""
    old_file = Path(old_path).resolve()
    new_file = Path(new_path).resolve()
    old = load_manifest(old_file)
    new = load_manifest(new_file)
    report = DiffReport(old["name"], new["name"], old_file.name, new_file.name)
    _diff_command(old, new, report)
    _diff_note(old, new, report)
    _diff_environment(old, new, report)
    _diff_git(old, new, report)
    _diff_artifacts("inputs", old, new, old_file.parent, new_file.parent, report)
    _diff_artifacts("outputs", old, new, old_file.parent, new_file.parent, report)
    _diff_claims(old, new, report)
    _diff_execution(old, new, report)
    return report


__all__ = ["Change", "DiffReport", "diff_manifests", "format_diff_json"]
