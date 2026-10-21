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
import math
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from sciproof.claims import MISSING, claim_text, evaluate, json_safe
from sciproof.environment import (
    clean_environment,
    hardware_info,
    interpreter_info,
    platform_info,
    selected_env_vars,
)
from sciproof.errors import ConfirmationRequired, UnsafePathError, UsageError
from sciproof.gitinfo import git_info
from sciproof.hashing import sha256_bytes, sha256_file
from sciproof.manifest import load_manifest
from sciproof.numeric import NumericFormatError, compare_detailed, load_numeric
from sciproof.paths import resolve_inside
from sciproof.policy import apply_policy, load_policy
from sciproof.runner import (
    CommandInterrupted,
    CommandNotRunnable,
    CommandTimeout,
    run_command,
)
from sciproof.signing import NAMESPACE, check_signature
from sciproof.textutil import safe_text
from sciproof.treehash import TreeDiff, TreeError, diff_trees, scan_tree, tree_digest

DEFAULT_RERUN_TIMEOUT_SECONDS = 3600.0
# How a numeric tolerance is applied (docs/COMPARISON.md has the full semantics).
NUMERIC_RULE = (
    "|a - b| <= max(rtol * max(|a|, |b|), atol); NaN equals NaN; infinities must match in sign"
)
MAX_LISTED_DIFFERENCES = 10


class Status(StrEnum):
    PASS = "PASS"  # noqa: S105  (a verdict, not a password)
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


REPORT_SCHEMA = "sciproof.report"
REPORT_SCHEMA_VERSION = "1.0"
MAX_SUBJECTS_IN_REASON = 5

# Statements no verification by SciProof can make, whatever the verdict.
ALWAYS_NOT_CHECKED = (
    "scientific validity: a matching result is not shown to be correct",
    "files that were not declared as inputs or outputs",
    "authenticity: manifests are unsigned, so who produced this evidence is not verified",
)
UNSIGNED_ITEM = ALWAYS_NOT_CHECKED[2]
SIGNED_ITEM = (
    "whether the experiment was run honestly: a good signature shows only that the holder of an "
    "allowed key vouched for this manifest"
)


@dataclass
class Check:
    status: Status
    subject: str
    message: str
    #: Facts the check used (digests, tolerances, ...); ``kind`` names the sort of check.
    evidence: dict[str, Any] | None = None


@dataclass
class Report:
    name: str
    rerun_requested: bool
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    rerun_outputs_dir: str | None = None
    rerun_executed: bool = False
    #: True when the rerun got a reduced environment (--clean-env).
    clean_env: bool = False
    #: Names (never values) of the variables handed to a --clean-env rerun.
    env_passed: list[str] = field(default_factory=list)
    #: Outcome of the signature check (``--allowed-signers``), or ``None`` if not asked for.
    signature: dict[str, Any] | None = None
    #: True once the manifest's claims have been looked at.
    claims_checked: bool = False
    #: The policy that was applied (file name, SHA-256, version), or ``None``.
    policy: dict[str, Any] | None = None

    def add(
        self,
        status: Status,
        subject: str,
        message: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.checks.append(Check(status, subject, message, evidence))

    @property
    def status(self) -> Status:
        statuses = {check.status for check in self.checks}
        if Status.FAIL in statuses:
            return Status.FAIL
        if Status.INCONCLUSIVE in statuses or not statuses:
            return Status.INCONCLUSIVE
        return Status.PASS

    def to_markdown(self) -> str:
        """The report as GitHub-flavoured Markdown (a CI summary or a pull-request comment)."""
        from sciproof.markdown import report_markdown

        return report_markdown(self)

    def to_dict(self) -> dict[str, Any]:
        """The report as plain data, in the ``sciproof.report`` JSON schema (docs/REPORT.md)."""
        return report_to_dict(self)

    @property
    def reason(self) -> str:
        """Why the verdict is what it is, in one sentence (the aggregation rule applied)."""

        def subjects(status: Status) -> str:
            names = [c.subject for c in self.checks if c.status is status]
            shown = ", ".join(names[:MAX_SUBJECTS_IN_REASON])
            more = len(names) - MAX_SUBJECTS_IN_REASON
            return f"{len(names)} ({shown}{f', and {more} more' if more > 0 else ''})"

        if self.status is Status.FAIL:
            return (
                f"FAIL because {subjects(Status.FAIL)} check(s) contradict the evidence; "
                "one failed check is enough"
            )
        if self.status is Status.INCONCLUSIVE:
            if not self.checks:
                return "INCONCLUSIVE because no check could be performed"
            return (
                f"INCONCLUSIVE because no check failed but {subjects(Status.INCONCLUSIVE)} "
                "could not be carried out; missing evidence is never counted as success"
            )
        return (
            f"PASS because all {len(self.checks)} performed checks succeeded; this covers only "
            "the checks listed above, not what is under 'not checked'"
        )

    @property
    def not_checked(self) -> list[str]:
        """What this verification did *not* establish."""
        items = list(ALWAYS_NOT_CHECKED)
        if self.signature is not None and self.signature["status"] == "good":
            items[items.index(UNSIGNED_ITEM)] = SIGNED_ITEM
        if self.claims_checked:
            items.append(
                "whether a claim is the right criterion: a claim that holds shows only that the "
                "value satisfies the comparison, not that the comparison is meaningful"
            )
        if self.rerun_executed:
            items += [
                "stdout and stderr of the rerun: compared for information only, not in the verdict",
                "environment reconstruction: differences are reported, not reproduced",
                "other machines or platforms: one re-execution on this machine only",
            ]
        else:
            items.insert(0, "reproducibility: the recorded command was not executed")
        return items


# --------------------------------------------------------------------------------------------
# integrity checks (never execute anything)
# --------------------------------------------------------------------------------------------


def _digest_evidence(
    kind: str, path: str, expected: str | None, actual: str | None
) -> dict[str, Any]:
    return {"kind": kind, "path": path, "expected_sha256": expected, "actual_sha256": actual}


@dataclass
class _State:
    """What is at a declared path now: a file (its SHA-256) or a directory (its tree digest)."""

    directory: bool
    exists: bool
    digest: str | None = None
    files: int | None = None
    diff: TreeDiff | None = None
    problem: str | None = None


def _state(path: Path, item: dict[str, Any]) -> _State:
    if item.get("kind", "file") != "directory":
        exists = path.is_file()
        return _State(False, exists, sha256_file(path)[0] if exists else None)
    if not path.is_dir():
        return _State(True, False)
    try:
        entries = scan_tree(path)
    except TreeError as exc:
        return _State(True, True, problem=str(exc))
    digest = tree_digest(entries)
    diff = None if digest == item["sha256"] else diff_trees(item.get("files") or [], entries)
    return _State(True, True, digest, len(entries), diff)


def _state_evidence(kind: str, item: dict[str, Any], state: _State) -> dict[str, Any]:
    evidence = _digest_evidence(kind, item["path"], item["sha256"], state.digest)
    if state.directory:
        evidence["artifact"] = "directory"
        evidence["files_expected"] = len(item.get("files") or [])
        evidence["files_actual"] = state.files
        evidence["tree"] = state.diff.to_dict() if state.diff else None
        if state.problem:
            evidence["problem"] = state.problem
    return evidence


def _changed_message(state: _State, default: str) -> str:
    """``default``, plus which files were added, removed or modified for a directory."""
    if state.directory and state.diff is not None:
        return f"{default}: {state.diff.describe()}"
    return default


def _unchanged_message(state: _State, item: dict[str, Any]) -> str:
    if state.directory:
        return f"unchanged ({len(item.get('files') or [])} files, tree SHA-256 matches)"
    return "unchanged (SHA-256 matches)"


def _check_signature(manifest_path: Path, allowed_signers: Path, report: Report) -> None:
    """Add the result of checking ``<manifest>.sig`` (a missing signature is not a pass)."""
    result = check_signature(manifest_path, allowed_signers)
    evidence = {
        "kind": "manifest_signature",
        "namespace": NAMESPACE,
        "signature_file": result.signature_file,
        "principal": result.principal,
        "key_type": result.key_type,
        "fingerprint": result.fingerprint,
    }
    report.signature = {"status": result.status, **evidence}
    status = {
        "good": Status.PASS,
        "bad": Status.FAIL,
    }.get(result.status, Status.INCONCLUSIVE)
    report.add(status, "manifest signature", result.message, evidence)


def _check_claims(
    base: Path,
    manifest: dict[str, Any],
    report: Report,
    phase: str,
    intact: set[str] | None = None,
) -> None:
    """Check the manifest's claims on the recorded outputs (``phase="recorded"``) or on the
    outputs of a rerun (``phase="rerun"``).

    On the recorded files a claim is only judged if the output still has the recorded hash;
    otherwise it is ``INCONCLUSIVE``, because a claim about a modified file says nothing about
    the evidence.
    """
    claims = manifest.get("claims") or []
    if not claims:
        return
    report.claims_checked = True
    cache: dict[str, dict[str, object] | str] = {}
    for claim in claims:
        output = claim["output"]
        subject = ("rerun claim " if phase == "rerun" else "claim ") + claim_text(claim)
        evidence: dict[str, Any] = {
            "kind": "claim",
            "phase": phase,
            "output": output,
            "location": claim["location"],
            "op": claim["op"],
            "value": claim["value"],
        }
        if claim["op"] == "~=":
            evidence["rtol"], evidence["atol"] = claim["rtol"], claim["atol"]
        if phase == "recorded" and output not in (intact or set()):
            report.add(
                Status.INCONCLUSIVE,
                subject,
                f"not judged: {output} no longer matches the recorded evidence, so the claim "
                "says nothing about it",
                evidence,
            )
            continue
        if output not in cache:
            try:
                path = resolve_inside(base, output)
                if not path.is_file():
                    cache[output] = "the rerun did not produce this file"
                else:
                    cache[output] = dict(load_numeric(path))
            except (UnsafePathError, NumericFormatError) as exc:
                cache[output] = f"cannot be read: {exc}"
        leaves = cache[output]
        if isinstance(leaves, str):
            status = Status.INCONCLUSIVE if "did not produce" in leaves else Status.FAIL
            report.add(status, subject, f"not judged: {output} {leaves}", evidence)
            continue
        result = evaluate(claim, leaves)
        evidence["found"] = None if result.found is MISSING else json_safe(result.found)
        evidence["present"] = result.found is not MISSING
        report.add(Status.PASS if result.holds else Status.FAIL, subject, result.message, evidence)


def _resolve(base: Path, relative: str, report: Report, subject: str) -> Path | None:
    try:
        return resolve_inside(base, relative)
    except UnsafePathError as exc:
        report.add(Status.FAIL, subject, f"unsafe path: {exc}")
        return None


def _check_inputs(base: Path, manifest: dict[str, Any], report: Report) -> bool:
    """Return True if every declared input is present and unmodified."""
    intact = True
    for item in manifest["inputs"]:
        subject = f"input {item['path']}"
        path = _resolve(base, item["path"], report, subject)
        if path is None:
            intact = False
        else:
            state = _state(path, item)
            evidence = _state_evidence("input_integrity", item, state)
            if state.problem:
                intact = False
                report.add(Status.FAIL, subject, f"cannot be checked: {state.problem}", evidence)
            elif not state.exists:
                intact = False
                what = "directory" if state.directory else "file"
                report.add(
                    Status.INCONCLUSIVE,
                    subject,
                    f"not available, so nothing can be said about it (restore the {what} "
                    "to verify)",
                    evidence,
                )
            elif state.digest == item["sha256"]:
                report.add(Status.PASS, subject, _unchanged_message(state, item), evidence)
            else:
                intact = False
                report.add(
                    Status.FAIL,
                    subject,
                    _changed_message(state, "modified since capture (SHA-256 differs)"),
                    evidence,
                )
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
            _digest_evidence("snapshot_integrity", snapshot["path"], snapshot["sha256"], None),
        )
        return None
    actual = sha256_file(path)[0]
    if actual != snapshot["sha256"]:
        report.add(
            Status.FAIL,
            f"{subject} snapshot",
            "recorded copy was modified (SHA-256 differs)",
            _digest_evidence("snapshot_integrity", snapshot["path"], snapshot["sha256"], actual),
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
        state = _state(path, item)
        exists, actual = state.exists, state.digest
        evidence = _state_evidence("output_integrity", item, state)
        if state.problem:
            report.add(Status.FAIL, subject, f"cannot be checked: {state.problem}", evidence)
        elif not item["present"]:
            if exists:
                report.add(Status.FAIL, subject, "was recorded as absent but now exists", evidence)
            else:
                report.add(Status.PASS, subject, "still absent, as recorded", evidence)
        elif not exists:
            report.add(
                Status.FAIL,
                subject,
                "missing (it existed when the evidence was captured)",
                evidence,
            )
        elif actual == item["sha256"]:
            report.add(Status.PASS, subject, _unchanged_message(state, item), evidence)
        else:
            report.add(
                Status.FAIL,
                subject,
                _changed_message(state, "modified since capture (SHA-256 differs)"),
                evidence,
            )
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
    differences.extend(_compare_hardware(recorded_env.get("hardware")))
    return differences


def _compare_hardware(recorded: dict[str, Any] | None) -> list[str]:
    """Differences between the recorded hardware and this machine (only if it was recorded)."""
    if recorded is None:
        return []
    now = hardware_info()
    differences = []
    if now["cpu_count"] != recorded["cpu_count"]:
        differences.append(f"CPU count: {recorded['cpu_count']} was used, now {now['cpu_count']}")
    old_gpus = [(g["name"], g["driver"]) for g in recorded["gpus"]]
    new_gpus = [(g["name"], g["driver"]) for g in now["gpus"]]
    if old_gpus != new_gpus and (
        recorded["gpu_probe"] == now["gpu_probe"] == "nvidia-smi" or old_gpus or new_gpus
    ):
        differences.append(f"GPUs: {_gpu_text(old_gpus)} were used, now {_gpu_text(new_gpus)}")
    return differences


def _gpu_text(gpus: list[tuple[str, str]]) -> str:
    return ", ".join(f"{name} (driver {driver})" for name, driver in gpus) or "none"


# --------------------------------------------------------------------------------------------
# rerun
# --------------------------------------------------------------------------------------------


def _describe_command(manifest: dict[str, Any]) -> str:
    return " ".join(safe_text(part, limit=200) for part in manifest["command"]["argv"])


def _rerun_prompt(base: Path, manifest: dict[str, Any], env_names: list[str] | None) -> str:
    if env_names is None:
        environment = (
            "It also receives ALL of your environment variables, including any credentials "
            "(use --clean-env to pass only a minimal set). "
        )
    else:
        environment = (
            f"It receives a reduced environment: {', '.join(env_names) or '(nothing)'}. "
            "Files and the network remain fully accessible to it. "
        )
    return (
        "SciProof is about to EXECUTE the command recorded in this manifest:\n\n"
        f"    {_describe_command(manifest)}\n\n"
        f"    working directory: {base}\n\n"
        "It will run with your current user's permissions and can read, change or delete "
        "anything you can. "
        + environment
        + "A manifest is untrusted input: only continue if you trust its "
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
    exit_evidence = {
        "kind": "exit_code",
        "recorded": recorded["exit_code"],
        "rerun": result.exit_code,
    }
    if result.exit_code == recorded["exit_code"]:
        report.add(Status.PASS, "exit code", f"{result.exit_code}, same as recorded", exit_evidence)
    else:
        report.add(
            Status.FAIL,
            "exit code",
            f"rerun exited with {result.exit_code}, recorded {recorded['exit_code']}",
            exit_evidence,
        )

    for item in manifest["outputs"]:
        subject = f"rerun output {item['path']}"
        path = _resolve(base, item["path"], report, subject)
        if path is None:
            continue
        state = _state(path, item)
        exists = state.exists
        if state.problem:
            report.add(
                Status.FAIL,
                subject,
                f"cannot be compared: {state.problem}",
                _state_evidence("rerun_output_exact", item, state),
            )
            continue
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
            evidence = _state_evidence("rerun_output_exact", item, state)
            if state.digest == item["sha256"]:
                report.add(
                    Status.PASS,
                    subject,
                    "identical to the recorded output (exact SHA-256)",
                    evidence,
                )
            elif state.directory:
                report.add(
                    Status.FAIL,
                    subject,
                    _changed_message(state, "differs from the recorded directory"),
                    evidence,
                )
            else:
                report.add(
                    Status.FAIL,
                    subject,
                    "differs from the recorded output (exact SHA-256 comparison). If small "
                    "numerical differences are acceptable, capture it with --output-numeric",
                    evidence,
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
        numeric_evidence: dict[str, Any] = {
            "kind": "rerun_output_numeric",
            "path": item["path"],
            "rtol": rtol,
            "atol": atol,
            "rule": NUMERIC_RULE,
        }
        try:
            details = compare_detailed(load_numeric(snapshot), load_numeric(path), rtol, atol)
        except NumericFormatError as exc:
            report.add(
                Status.FAIL,
                subject,
                f"cannot be compared numerically: {exc}",
                {**numeric_evidence, "differences": None, "error": str(exc)},
            )
            continue
        differences = [d.message for d in details]
        numeric_evidence["differences"] = differences
        numeric_evidence["difference_details"] = [d.to_dict() for d in details]
        if differences:
            report.add(
                Status.FAIL,
                subject,
                "outside the declared tolerance: " + "; ".join(differences),
                numeric_evidence,
            )
        else:
            report.add(
                Status.PASS,
                subject,
                f"numerically equivalent (rtol={rtol}, atol={atol})",
                numeric_evidence,
            )

    for stream in ("stdout", "stderr"):
        text = getattr(result, stream)
        same = sha256_bytes(text) == recorded[stream]["sha256"]
        relation = "identical to" if same else "different from"
        report.info.append(
            f"{stream} of the rerun is {relation} the recorded {stream} (informational only)"
        )


def _same_content(current: Path, backup: Path, directory: bool) -> bool:
    """True if ``current`` is identical to the backup taken before the rerun."""
    if not directory:
        return sha256_file(current)[0] == sha256_file(backup)[0]
    try:
        return tree_digest(scan_tree(current)) == tree_digest(scan_tree(backup))
    except TreeError:
        return False  # cannot be shown identical, so it is treated as changed and moved aside


def _rerun_dir_name(stamp: str, counter: int) -> str:
    return f".sciproof-rerun-{stamp}" + (f"-{counter}" if counter else "")


def _rerun(
    base: Path,
    manifest: dict[str, Any],
    snapshots: dict[str, Path | None],
    report: Report,
    timeout: float | None,
    env: dict[str, str] | None = None,
) -> None:
    """Run the command once. The tree is left as it was: new outputs are moved aside."""
    output_paths = [item["path"] for item in manifest["outputs"]]
    directory_outputs = {i["path"] for i in manifest["outputs"] if i.get("kind") == "directory"}
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
            if relative in directory_outputs and target.is_dir():
                (backup_root / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(target, backup_root / relative, symlinks=True)
                had_original.add(relative)
            elif relative not in directory_outputs and target.is_file():
                (backup_root / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_root / relative)
                had_original.add(relative)

        # Start from a clean slate. A stale output left over from the recorded run would
        # otherwise "reproduce" the result even if the command never wrote it (a bug, a wrong
        # path, an early exit). The originals are backed up above and restored afterwards.
        for relative in sorted(had_original):
            target = resolve_inside(base, relative)
            if relative in directory_outputs:
                shutil.rmtree(target)
            else:
                target.unlink()

        try:
            argv = manifest["command"]["argv"]
            result = (
                run_command(argv, base, timeout)
                if env is None
                else run_command(argv, base, timeout, env)
            )
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

        report.rerun_executed = True
        _compare_rerun(base, manifest, snapshots, result, report)
        _check_claims(base, manifest, report, "rerun")

        # A rerun must not damage the evidence it is checked against.
        for item in manifest["inputs"]:
            state = _state(resolve_inside(base, item["path"]), item)
            if not state.exists or state.digest != item["sha256"]:
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
            is_directory = relative in directory_outputs
            exists = target.is_dir() if is_directory else target.is_file()
            if exists:
                if relative in had_original and _same_content(
                    target, backup_root / relative, is_directory
                ):
                    continue  # the rerun did not change this output: nothing to move or say
                if moved_to is None:
                    moved_to = base / _rerun_dir_name(stamp, counter)
                destination = moved_to / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), destination)
            if relative in had_original:
                target.parent.mkdir(parents=True, exist_ok=True)
                if is_directory:
                    shutil.copytree(backup_root / relative, target, symlinks=True)
                else:
                    shutil.copy2(backup_root / relative, target)
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
    clean_env: bool = False,
    pass_env: Sequence[str] = (),
    allowed_signers: Path | str | None = None,
    policy: Path | str | None = None,
) -> Report:
    """Verify ``manifest_path``.

    Without ``rerun`` nothing is executed and only artifact integrity is checked. With ``rerun``
    the recorded command is executed after confirmation (``assume_yes`` or the ``confirm``
    callback); without either, ``ConfirmationRequired`` is raised and nothing runs. With
    ``policy`` (a TOML file, see ``sciproof.policy``) every rule of the policy is added to the
    report as a check; an invalid policy is an error before anything else happens. Raises
    ``ManifestError`` for an unreadable or invalid manifest.
    """
    loaded = load_policy(policy) if policy is not None else None
    report, manifest = _verify(
        manifest_path,
        rerun=rerun,
        assume_yes=assume_yes,
        strict_env=strict_env,
        timeout=timeout,
        confirm=confirm,
        clean_env=clean_env,
        pass_env=pass_env,
        allowed_signers=allowed_signers,
    )
    if loaded is not None:
        apply_policy(loaded, manifest, report)
    return report


def _verify(
    manifest_path: Path,
    *,
    rerun: bool = False,
    assume_yes: bool = False,
    strict_env: bool = False,
    timeout: float | None = DEFAULT_RERUN_TIMEOUT_SECONDS,
    confirm: Callable[[str], bool] | None = None,
    clean_env: bool = False,
    pass_env: Sequence[str] = (),
    allowed_signers: Path | str | None = None,
) -> tuple[Report, dict[str, Any]]:
    """Verify ``manifest_path``.

    Without ``rerun`` nothing is executed and only artifact integrity is checked. With
    ``rerun`` the recorded command is executed after confirmation (``assume_yes`` or the
    ``confirm`` callback); without either, ``ConfirmationRequired`` is raised and nothing runs.
    Raises ``ManifestError`` for an unreadable or invalid manifest.
    """
    if timeout is not None and not (math.isfinite(timeout) and timeout > 0):
        raise UsageError("timeout must be a positive number of seconds")
    if clean_env and not rerun:
        raise UsageError("--clean-env only applies to --rerun")
    if pass_env and not clean_env:
        raise UsageError("--pass-env needs --clean-env (without it nothing is filtered)")
    manifest_path = manifest_path.resolve()
    manifest = load_manifest(manifest_path)
    base = manifest_path.parent
    report = Report(name=manifest["name"], rerun_requested=rerun)

    if allowed_signers is not None:
        _check_signature(manifest_path, Path(allowed_signers), report)

    inputs_intact = _check_inputs(base, manifest, report)
    snapshots = _check_outputs(base, manifest, report)
    intact = {
        item["path"]
        for item in manifest["outputs"]
        if any(
            c.subject == f"output {item['path']}" and c.status is Status.PASS for c in report.checks
        )
    }
    _check_claims(base, manifest, report, "recorded", intact)

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
        return report, manifest

    if not inputs_intact:
        report.add(
            Status.INCONCLUSIVE,
            "rerun",
            "not attempted: declared inputs are missing, modified or unsafe, so a rerun would "
            "not be comparable with the recorded run",
        )
        return report, manifest

    declared = [item["path"] for item in manifest["inputs"] + manifest["outputs"]]
    try:
        for relative in declared:
            resolve_inside(base, relative)
    except UnsafePathError as exc:
        report.add(Status.INCONCLUSIVE, "rerun", f"not attempted: unsafe path ({exc})")
        return report, manifest

    rerun_env: dict[str, str] | None = None
    unset: list[str] = []
    if clean_env:
        rerun_env, unset = clean_environment(pass_env)
        report.clean_env = True
        report.env_passed = sorted(rerun_env)
    prompt = _rerun_prompt(base, manifest, report.env_passed if clean_env else None)
    if not assume_yes:
        if confirm is None:
            raise ConfirmationRequired(
                "refusing to execute the recorded command without confirmation; re-run with "
                "--yes after reading the command (see 'sciproof inspect')"
            )
        if not confirm(prompt):
            report.add(Status.INCONCLUSIVE, "rerun", "declined by the user; nothing was executed")
            return report, manifest

    # Only now, after confirmation, is anything outside SciProof started (the interpreter probe).
    differences = _compare_environment(base, manifest)
    if differences:
        if strict_env:
            report.add(
                Status.INCONCLUSIVE,
                "environment",
                "differs from the recorded one (--strict-env): " + "; ".join(differences),
            )
            return report, manifest
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

    _rerun(base, manifest, snapshots, report, timeout, rerun_env)
    if clean_env:
        report.info.append(
            "the rerun received a reduced environment (--clean-env); this limits which "
            "variables it can read, not which files or network it can reach"
        )
        if unset:
            report.info.append("requested with --pass-env but not set: " + ", ".join(unset))
    report.info.append(
        "scope: integrity and one re-execution. Bitwise agreement across different hardware "
        "or platforms is not expected; agreement is not a proof of scientific correctness."
    )
    return report, manifest


def report_to_dict(report: Report) -> dict[str, Any]:
    """The report as plain data. Strings are not escaped here; ``json.dumps`` does that."""
    counts = {status.value: 0 for status in Status}
    for check in report.checks:
        counts[check.status.value] += 1
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": REPORT_SCHEMA_VERSION,
        "name": report.name,
        "result": report.status.value,
        "reason": report.reason,
        "counts": counts,
        "rerun_requested": report.rerun_requested,
        "rerun_executed": report.rerun_executed,
        "clean_env": report.clean_env,
        "env_passed": list(report.env_passed),
        "signature": report.signature,
        "policy": report.policy,
        "checks": [
            {
                "status": c.status.value,
                "subject": c.subject,
                "message": c.message,
                "evidence": c.evidence,
            }
            for c in report.checks
        ],
        "not_checked": report.not_checked,
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
    lines.append(f"  basis: {report.reason}")
    lines.extend(f"  not checked: {item}" for item in report.not_checked)
    lines.append("")
    lines.append(f"RESULT: {report.status.value}")
    return "\n".join(lines)
