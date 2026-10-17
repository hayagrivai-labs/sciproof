"""Verify a manifest: integrity checks and, only on explicit request, re-execution.

Verdicts
--------
PASS          every check that was performed succeeded.
FAIL          at least one artifact or result was found to differ from the evidence.
INCONCLUSIVE  evidence is incomplete or the check could not be carried out (missing input,
              missing numeric snapshot, command could not be started, run was interrupted,
              rerun declined, ...). INCONCLUSIVE is never reported as success.

A report always states which checks were *not* performed, because integrity of files is a
much weaker statement than reproducibility of a computation (see docs/CONCEPTS.md).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from sciproof.environment import interpreter_info, platform_info, selected_env_vars
from sciproof.errors import ConfirmationRequired, UnsafePathError
from sciproof.gitinfo import git_info
from sciproof.hashing import sha256_bytes, sha256_file
from sciproof.manifest import load_manifest
from sciproof.numeric import NumericFormatError, compare, load_numeric
from sciproof.paths import resolve_inside
from sciproof.runner import (
    CommandInterrupted,
    CommandNotRunnable,
    CommandTimeout,
    run_command,
)
from sciproof.textutil import safe_text
from sciproof.tree import TreeError, diff_tree, list_tree, tree_digest

DEFAULT_RERUN_TIMEOUT_SECONDS = 3600.0
MAX_LISTED_DIFFERENCES = 10


class Status(StrEnum):
    PASS = "PASS"  # noqa: S105  (a verdict, not a password)
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class Check:
    status: Status
    subject: str
    message: str


@dataclass
class Report:
    name: str
    rerun_requested: bool
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    rerun_outputs_dir: str | None = None

    def add(self, status: Status, subject: str, message: str) -> None:
        self.checks.append(Check(status, subject, message))

    @property
    def status(self) -> Status:
        statuses = {check.status for check in self.checks}
        if Status.FAIL in statuses:
            return Status.FAIL
        if Status.INCONCLUSIVE in statuses or not statuses:
            return Status.INCONCLUSIVE
        return Status.PASS


# --------------------------------------------------------------------------------------------
# integrity checks (never execute anything)
# --------------------------------------------------------------------------------------------


def _resolve(base: Path, relative: str, report: Report, subject: str) -> Path | None:
    try:
        return resolve_inside(base, relative)
    except UnsafePathError as exc:
        report.add(Status.FAIL, subject, f"unsafe path: {exc}")
        return None


def _is_directory(item: dict[str, Any]) -> bool:
    return item.get("kind") == "directory"


def _exists(path: Path, item: dict[str, Any]) -> bool:
    return path.is_dir() if _is_directory(item) else path.is_file()


def _matches_record(path: Path, item: dict[str, Any]) -> tuple[bool, str]:
    """Compare an existing file or directory with its record: ``(matches, difference)``."""
    if not _is_directory(item):
        return sha256_file(path)[0] == item["sha256"], "SHA-256 differs"
    try:
        current = list_tree(path)
    except TreeError as exc:
        return False, f"cannot be listed: {exc}"
    if tree_digest(current) == item["sha256"]:
        return True, ""
    return False, "; ".join(diff_tree(item["files"], current))


def _describe_match(item: dict[str, Any]) -> str:
    if _is_directory(item):
        return f"unchanged (all {len(item['files'])} files match)"
    return "unchanged (SHA-256 matches)"


def _check_inputs(base: Path, manifest: dict[str, Any], report: Report) -> bool:
    """Return True if every declared input is present and unmodified."""
    intact = True
    for item in manifest["inputs"]:
        subject = f"input {item['path']}"
        path = _resolve(base, item["path"], report, subject)
        if path is None:
            intact = False
        elif not _exists(path, item):
            intact = False
            report.add(
                Status.INCONCLUSIVE,
                subject,
                "not available, so nothing can be said about it (restore it to verify)",
            )
        else:
            matches, difference = _matches_record(path, item)
            if matches:
                report.add(Status.PASS, subject, _describe_match(item))
            else:
                intact = False
                report.add(Status.FAIL, subject, f"modified since capture ({difference})")
    return intact


def _check_snapshot(base: Path, item: dict[str, Any], report: Report, subject: str) -> Path | None:
    snapshot = item["compare"]["snapshot"]
    path = _resolve(base, snapshot["path"], report, f"{subject} snapshot")
    if path is None:
        return None
    if not path.is_file():
        report.add(
            Status.INCONCLUSIVE,
            f"{subject} snapshot",
            f"recorded copy {snapshot['path']} is missing; numeric comparison is impossible",
        )
        return None
    if sha256_file(path)[0] != snapshot["sha256"]:
        report.add(
            Status.FAIL, f"{subject} snapshot", "recorded copy was modified (SHA-256 differs)"
        )
        return None
    return path


def _check_outputs(base: Path, manifest: dict[str, Any], report: Report) -> dict[str, Path | None]:
    """Integrity of recorded outputs. Returns verified snapshot paths of numeric outputs."""
    snapshots: dict[str, Path | None] = {}
    for item in manifest["outputs"]:
        subject = f"output {item['path']}"
        path = _resolve(base, item["path"], report, subject)
        if item["compare"]["mode"] == "numeric":
            snapshots[item["path"]] = _check_snapshot(base, item, report, subject)
        if path is None:
            continue
        exists = _exists(path, item)
        if not item["present"]:
            if exists:
                report.add(Status.FAIL, subject, "was recorded as absent but now exists")
            else:
                report.add(Status.PASS, subject, "still absent, as recorded")
        elif not exists:
            report.add(Status.FAIL, subject, "missing (it existed when the evidence was captured)")
        else:
            matches, difference = _matches_record(path, item)
            if matches:
                report.add(Status.PASS, subject, _describe_match(item))
            else:
                report.add(Status.FAIL, subject, f"modified since capture ({difference})")
    return snapshots


# --------------------------------------------------------------------------------------------
# environment comparison
# --------------------------------------------------------------------------------------------


def _compare_environment(base: Path, manifest: dict[str, Any]) -> list[str]:
    """Return human-readable differences between the recorded and the current environment."""
    differences: list[str] = []
    recorded_env = manifest["environment"]
    argv0 = manifest["command"]["argv"][0]
    current, _ = interpreter_info(argv0, base)

    if current["version"] != recorded_env["python"]["version"]:
        differences.append(
            f"Python {recorded_env['python']['version']} was used, now {current['version']}"
        )
    now_platform = platform_info()
    old_platform = recorded_env["platform"]
    for key in ("system", "machine"):
        if old_platform.get(key) != now_platform[key]:
            differences.append(
                f"{key}: {old_platform.get(key)!r} was used, now {now_platform[key]!r}"
            )

    now_packages = {name.lower().replace("_", "-"): ver for name, ver in current["packages"]}
    package_notes = []
    for dep in manifest["dependencies"]:
        key = dep["name"].lower().replace("_", "-")
        if key not in now_packages:
            package_notes.append(f"package {dep['name']}=={dep['version']} is not installed")
        elif now_packages[key] != dep["version"]:
            package_notes.append(
                f"package {dep['name']}: {dep['version']} was used, now {now_packages[key]}"
            )
    differences.extend(package_notes[:MAX_LISTED_DIFFERENCES])
    if len(package_notes) > MAX_LISTED_DIFFERENCES:
        differences.append(
            f"... and {len(package_notes) - MAX_LISTED_DIFFERENCES} more package differences"
        )

    now_vars = selected_env_vars()
    recorded_vars = recorded_env["env_vars"]
    # Compare in both directions: a variable that is set now but was not set at capture time
    # (for example OMP_NUM_THREADS) can change numerical results just like a changed one.
    for name in sorted(set(recorded_vars) | set(now_vars)):
        if name not in now_vars:
            differences.append(f"environment variable {name} was set at capture time, not now")
        elif name not in recorded_vars:
            differences.append(f"environment variable {name} is set now, was not at capture time")
        elif now_vars[name] != recorded_vars[name]:
            differences.append(f"environment variable {name} differs from the recorded value")
    return differences


# --------------------------------------------------------------------------------------------
# rerun
# --------------------------------------------------------------------------------------------


def _describe_command(manifest: dict[str, Any]) -> str:
    return " ".join(safe_text(part, limit=200) for part in manifest["command"]["argv"])


def _rerun_prompt(base: Path, manifest: dict[str, Any]) -> str:
    return (
        "SciProof is about to EXECUTE the command recorded in this manifest:\n\n"
        f"    {_describe_command(manifest)}\n\n"
        f"    working directory: {base}\n\n"
        "It will run with your current user's permissions and can read, change or delete "
        "anything you can. A manifest is untrusted input: only continue if you trust its "
        "source and have read the command above."
    )


def _compare_rerun(
    base: Path,
    manifest: dict[str, Any],
    snapshots: dict[str, Path | None],
    result: Any,
    report: Report,
) -> None:
    recorded = manifest["execution"]
    if result.exit_code == recorded["exit_code"]:
        report.add(Status.PASS, "exit code", f"{result.exit_code}, same as recorded")
    else:
        report.add(
            Status.FAIL,
            "exit code",
            f"rerun exited with {result.exit_code}, recorded {recorded['exit_code']}",
        )

    for item in manifest["outputs"]:
        subject = f"rerun output {item['path']}"
        path = _resolve(base, item["path"], report, subject)
        if path is None:
            continue
        exists = _exists(path, item)
        if not item["present"]:
            if exists:
                report.add(
                    Status.FAIL, subject, "was absent in the recorded run but the rerun created it"
                )
            else:
                report.add(Status.PASS, subject, "absent, as recorded")
            continue
        if not exists:
            report.add(Status.FAIL, subject, "the rerun did not produce this file")
            continue
        if item["compare"]["mode"] == "exact":
            matches, difference = _matches_record(path, item)
            if matches:
                report.add(Status.PASS, subject, "identical to the recorded output (exact SHA-256)")
            elif _is_directory(item):
                report.add(
                    Status.FAIL, subject, f"differs from the recorded directory: {difference}"
                )
            else:
                report.add(
                    Status.FAIL,
                    subject,
                    "differs from the recorded output (exact SHA-256 comparison). If small "
                    "numerical differences are acceptable, capture it with --output-numeric",
                )
            continue
        snapshot = snapshots.get(item["path"])
        if snapshot is None:
            report.add(
                Status.INCONCLUSIVE,
                subject,
                "recorded copy is unusable; cannot compare numerically",
            )
            continue
        rtol, atol = item["compare"]["rtol"], item["compare"]["atol"]
        try:
            differences = compare(load_numeric(snapshot), load_numeric(path), rtol, atol)
        except NumericFormatError as exc:
            report.add(Status.FAIL, subject, f"cannot be compared numerically: {exc}")
            continue
        if differences:
            report.add(
                Status.FAIL,
                subject,
                "outside the declared tolerance: " + "; ".join(differences),
            )
        else:
            report.add(Status.PASS, subject, f"numerically equivalent (rtol={rtol}, atol={atol})")

    for stream in ("stdout", "stderr"):
        text = getattr(result, stream)
        same = sha256_bytes(text) == recorded[stream]["sha256"]
        relation = "identical to" if same else "different from"
        report.info.append(
            f"{stream} of the rerun is {relation} the recorded {stream} (informational only)"
        )


def _copy_path(source: Path, destination: Path) -> None:
    """Copy a file or a whole directory tree (symlinks are copied as links, not followed)."""
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination)


def _rerun_dir_name(stamp: str, counter: int) -> str:
    return f".sciproof-rerun-{stamp}" + (f"-{counter}" if counter else "")


def _rerun(
    base: Path,
    manifest: dict[str, Any],
    snapshots: dict[str, Path | None],
    report: Report,
    timeout: float | None,
) -> None:
    """Run the command once. The tree is left as it was: new outputs are moved aside."""
    output_paths = [item["path"] for item in manifest["outputs"]]
    backup_root = Path(tempfile.mkdtemp(prefix="sciproof-backup-"))
    had_original: set[str] = set()
    # Directories that exist before the run must stay; ones the rerun creates are removed again.
    preexisting_dirs = {
        parent
        for relative in output_paths
        for parent in PurePosixPath(relative).parents
        if str(parent) != "." and (base / parent).is_dir()
    }
    try:
        for relative in output_paths:
            target = resolve_inside(base, relative)
            if target.is_file() or target.is_dir():
                (backup_root / relative).parent.mkdir(parents=True, exist_ok=True)
                _copy_path(target, backup_root / relative)
                had_original.add(relative)

        try:
            result = run_command(manifest["command"]["argv"], base, timeout)
        except CommandTimeout as exc:
            report.add(Status.INCONCLUSIVE, "rerun", str(exc))
            return
        except CommandInterrupted:
            report.add(
                Status.INCONCLUSIVE, "rerun", "interrupted by the user; originals were restored"
            )
            return
        except CommandNotRunnable as exc:
            report.add(Status.INCONCLUSIVE, "rerun", f"{exc} (is it installed here?)")
            return

        _compare_rerun(base, manifest, snapshots, result, report)

        # A rerun must not damage the evidence it is checked against.
        for item in manifest["inputs"]:
            path = resolve_inside(base, item["path"])
            if not _exists(path, item) or not _matches_record(path, item)[0]:
                report.add(
                    Status.FAIL,
                    f"input {item['path']}",
                    "was modified or removed by the rerun (inputs must be read-only)",
                )
    finally:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        moved_to: Path | None = None
        counter = 0
        while (base / _rerun_dir_name(stamp, counter)).exists():
            counter += 1  # two reruns within one second must not share a directory
        for relative in output_paths:
            target = resolve_inside(base, relative)
            if target.is_file() or target.is_dir():
                if moved_to is None:
                    moved_to = base / _rerun_dir_name(stamp, counter)
                destination = moved_to / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), destination)
            if relative in had_original:
                target.parent.mkdir(parents=True, exist_ok=True)
                _copy_path(backup_root / relative, target)
        for relative in output_paths:
            for parent in PurePosixPath(relative).parents:
                if str(parent) == "." or parent in preexisting_dirs:
                    continue
                try:
                    (base / parent).rmdir()  # only succeeds if the directory is empty
                except OSError:
                    break
        if moved_to is not None:
            report.rerun_outputs_dir = moved_to.name
        shutil.rmtree(backup_root, ignore_errors=True)


def verify(
    manifest_path: Path,
    *,
    rerun: bool = False,
    assume_yes: bool = False,
    strict_env: bool = False,
    timeout: float | None = DEFAULT_RERUN_TIMEOUT_SECONDS,
    confirm: Callable[[str], bool] | None = None,
) -> Report:
    """Verify ``manifest_path``.

    Without ``rerun`` nothing is executed and only artifact integrity is checked. With
    ``rerun`` the recorded command is executed after confirmation (``assume_yes`` or the
    ``confirm`` callback); without either, ``ConfirmationRequired`` is raised and nothing runs.
    Raises ``ManifestError`` for an unreadable or invalid manifest.
    """
    manifest_path = manifest_path.resolve()
    manifest = load_manifest(manifest_path)
    base = manifest_path.parent
    report = Report(name=manifest["name"], rerun_requested=rerun)

    inputs_intact = _check_inputs(base, manifest, report)
    snapshots = _check_outputs(base, manifest, report)

    recorded_git = manifest.get("git")
    if recorded_git and recorded_git["dirty"]:
        report.warnings.append(
            "the evidence was captured from a Git working tree with uncommitted changes; the "
            "recorded commit alone does not identify the code"
        )
    if manifest["execution"]["exit_code"] != 0:
        report.warnings.append(
            f"the recorded run exited with status {manifest['execution']['exit_code']}"
        )
    if manifest["environment"].get("source") == "sciproof-process":
        report.warnings.append(
            "the recorded Python version and packages describe the interpreter that ran "
            "SciProof, not necessarily the one that ran the command"
        )

    if not rerun:
        report.info.append(
            "scope: artifact integrity only. The recorded command was NOT executed, so this "
            "does not show that the results can be reproduced."
        )
        return report

    if not inputs_intact:
        report.add(
            Status.INCONCLUSIVE,
            "rerun",
            "not attempted: declared inputs are missing, modified or unsafe, so a rerun would "
            "not be comparable with the recorded run",
        )
        return report

    declared = [item["path"] for item in manifest["inputs"] + manifest["outputs"]]
    try:
        for relative in declared:
            resolve_inside(base, relative)
    except UnsafePathError as exc:
        report.add(Status.INCONCLUSIVE, "rerun", f"not attempted: unsafe path ({exc})")
        return report

    prompt = _rerun_prompt(base, manifest)
    if not assume_yes:
        if confirm is None:
            raise ConfirmationRequired(
                "refusing to execute the recorded command without confirmation; re-run with "
                "--yes after reading the command (see 'sciproof inspect')"
            )
        if not confirm(prompt):
            report.add(Status.INCONCLUSIVE, "rerun", "declined by the user; nothing was executed")
            return report

    # Only now, after confirmation, is anything outside SciProof started (the interpreter probe).
    differences = _compare_environment(base, manifest)
    if differences:
        if strict_env:
            report.add(
                Status.INCONCLUSIVE,
                "environment",
                "differs from the recorded one (--strict-env): " + "; ".join(differences),
            )
            return report
        report.warnings.append(
            "the environment differs from the recorded one: " + "; ".join(differences)
        )

    current_git = git_info(base)
    if recorded_git and current_git and recorded_git["commit"] != current_git["commit"]:
        report.warnings.append(
            f"the Git commit differs: recorded {recorded_git['commit'][:12]}, "
            f"now {current_git['commit'][:12]}"
        )
    if current_git and current_git["dirty"]:
        report.info.append("the current Git working tree has uncommitted changes")

    _rerun(base, manifest, snapshots, report, timeout)
    report.info.append(
        "scope: integrity and one re-execution. Bitwise agreement across different hardware "
        "or platforms is not expected; agreement is not a proof of scientific correctness."
    )
    return report


def report_to_dict(report: Report) -> dict[str, Any]:
    """The report as plain data. Strings are not escaped here; ``json.dumps`` does that."""
    return {
        "name": report.name,
        "result": report.status.value,
        "rerun_requested": report.rerun_requested,
        "checks": [
            {"status": c.status.value, "subject": c.subject, "message": c.message}
            for c in report.checks
        ],
        "warnings": list(report.warnings),
        "notes": list(report.info),
        "rerun_outputs_dir": report.rerun_outputs_dir,
    }


def format_report_json(report: Report) -> str:
    """Machine-readable report. ``ensure_ascii`` keeps control characters escaped."""
    return json.dumps(report_to_dict(report), indent=2)


def format_report(report: Report) -> str:
    lines = [f"SciProof verification of '{safe_text(report.name)}'", ""]
    for check in report.checks:
        lines.append(
            f"  [{check.status.value}] {safe_text(check.subject)}: {safe_text(check.message, 2000)}"
        )
    if report.warnings:
        lines.append("")
        lines.extend(f"  warning: {safe_text(w, 2000)}" for w in report.warnings)
    if report.info:
        lines.append("")
        lines.extend(f"  note: {safe_text(n, 2000)}" for n in report.info)
    if report.rerun_outputs_dir:
        lines.append(
            f"  note: files produced by the rerun were moved to {report.rerun_outputs_dir}/"
        )
    lines.append("")
    lines.append(f"RESULT: {report.status.value}")
    return "\n".join(lines)
