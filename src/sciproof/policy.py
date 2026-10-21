"""Verification policies: rules about the evidence itself, written down once and checked every time.

A policy is a small TOML file::

    version = 1

    [manifest]
    max_age_days = 30          # the evidence was captured at most 30 days ago
    require_exit_code = 0      # the recorded run exited with this status
    require_git = true         # a Git commit was recorded
    forbid_dirty_git = true    # ... and the working tree had no uncommitted changes
    require_signature = true   # the manifest is signed (needs --allowed-signers)
    min_claims = 1             # at least one claim is recorded

    [outputs]
    require_numeric = ["metrics.json"]   # compared with a tolerance, not by exact hash
    max_rtol = 1e-3                      # no numeric output may declare a looser tolerance
    max_atol = 1e-6

    [rerun]
    require = true             # the verification must include a rerun
    require_clean_env = true   # ... with a minimal environment

Policies are validated strictly: an unknown section or key, a value of the wrong type or a number
out of range is an error, never a guess. Every rule becomes one check of the report. A rule whose
evidence is missing (for example ``require_signature`` without ``--allowed-signers``) is
``INCONCLUSIVE``, not a pass. A policy says what evidence you require; it does not say that the
experiment is right.
"""

from __future__ import annotations

import hashlib
import math
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sciproof.errors import SciProofError
from sciproof.paths import validate_relative_path

if TYPE_CHECKING:
    from sciproof.verify import Report

POLICY_VERSION = 1
MAX_POLICY_BYTES = 64 * 1024
MAX_REQUIRED_OUTPUTS = 100
CLOCK_SKEW_SECONDS = 300

# section -> key -> expected kind
_SCHEMA: dict[str, dict[str, str]] = {
    "manifest": {
        "max_age_days": "positive",
        "require_exit_code": "int",
        "require_git": "bool",
        "forbid_dirty_git": "bool",
        "require_signature": "bool",
        "min_claims": "count",
    },
    "outputs": {
        "require_numeric": "paths",
        "max_rtol": "nonneg",
        "max_atol": "nonneg",
    },
    "rerun": {
        "require": "bool",
        "require_clean_env": "bool",
    },
}


class PolicyError(SciProofError):
    """A policy file is unreadable or invalid."""


@dataclass(frozen=True)
class Policy:
    """The rules of a policy file, as validated values (``None`` = no such rule)."""

    file: str
    sha256: str
    rules: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get(self, section: str, key: str) -> Any:
        return self.rules.get(section, {}).get(key)

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "sha256": self.sha256, "version": POLICY_VERSION}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_value(where: str, kind: str, value: Any) -> Any:
    if kind == "bool":
        if not isinstance(value, bool):
            raise PolicyError(f"{where}: expected true or false, got {value!r}")
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PolicyError(f"{where}: expected a whole number, got {value!r}")
    elif kind == "count":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PolicyError(f"{where}: expected a whole number >= 0, got {value!r}")
    elif kind in ("positive", "nonneg"):
        ok = _is_number(value) and math.isfinite(value)
        if not ok or (value <= 0 if kind == "positive" else value < 0):
            what = "greater than 0" if kind == "positive" else "0 or more"
            raise PolicyError(f"{where}: expected a finite number {what}, got {value!r}")
    elif kind == "paths":
        if not isinstance(value, list) or len(value) > MAX_REQUIRED_OUTPUTS:
            raise PolicyError(f"{where}: expected a list of at most {MAX_REQUIRED_OUTPUTS} paths")
        seen: set[str] = set()
        for item in value:
            try:
                validate_relative_path(item)
            except SciProofError as exc:
                raise PolicyError(f"{where}: {exc}") from None
            if item in seen:
                raise PolicyError(f"{where}: {item!r} is listed twice")
            seen.add(item)
    return value


def parse_policy(text: str, *, file: str = "policy") -> Policy:
    """Validate the text of a policy file."""
    try:
        # TOML 1.0 permits a UTF-8 BOM at the start of a file. Keep the original text for
        # the digest, but omit the BOM because tomllib does not accept it.
        data = tomllib.loads(text.removeprefix("\ufeff"))
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"{file}: not valid TOML: {exc}") from None
    if data.get("version") != POLICY_VERSION or isinstance(data.get("version"), bool):
        raise PolicyError(
            f"{file}: 'version = {POLICY_VERSION}' is required (got {data.get('version')!r})"
        )
    rules: dict[str, dict[str, Any]] = {}
    for section, content in data.items():
        if section == "version":
            continue
        if section not in _SCHEMA:
            raise PolicyError(
                f"{file}: unknown section [{section}]; use one of "
                f"{', '.join('[' + s + ']' for s in _SCHEMA)}"
            )
        if not isinstance(content, dict):
            raise PolicyError(f"{file}: [{section}] must be a table")
        rules[section] = {}
        for key, value in content.items():
            if key not in _SCHEMA[section]:
                raise PolicyError(
                    f"{file}: unknown rule {section}.{key}; known rules of [{section}]: "
                    f"{', '.join(_SCHEMA[section])}"
                )
            rules[section][key] = _check_value(
                f"{file}: {section}.{key}", _SCHEMA[section][key], value
            )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Policy(file=file, sha256=digest, rules=rules)


def load_policy(path: Path | str) -> Policy:
    """Read and validate a policy file."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_POLICY_BYTES:
            raise PolicyError(f"{path.name}: larger than {MAX_POLICY_BYTES} bytes")
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PolicyError(f"policy file not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise PolicyError(f"cannot read policy {path}: {exc}") from exc
    return parse_policy(text, file=path.name)


# --------------------------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------------------------


def _add(report: Report, status: str, rule: str, message: str, **evidence: Any) -> None:
    from sciproof.verify import Status

    report.add(
        Status(status), f"policy {rule}", message, {"kind": "policy", "rule": rule, **evidence}
    )


def _age_days(created_at: str, now: datetime) -> float:
    captured = datetime.fromisoformat(created_at)
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=UTC)
    return (now - captured).total_seconds() / 86400


def apply_policy(
    policy: Policy, manifest: dict[str, Any], report: Report, *, now: datetime | None = None
) -> None:
    """Add one check per rule of ``policy`` to ``report``."""
    now = now or datetime.now(UTC)
    report.policy = policy.to_dict()

    limit = policy.get("manifest", "max_age_days")
    if limit is not None:
        age = _age_days(manifest["created_at"], now)
        if age < -CLOCK_SKEW_SECONDS / 86400:
            _add(
                report,
                "FAIL",
                "manifest.max_age_days",
                "the manifest says it was captured in the future",
                limit=limit,
            )
        elif age <= limit:
            _add(
                report,
                "PASS",
                "manifest.max_age_days",
                f"captured {age:.1f} days ago (at most {limit:g} allowed)",
                limit=limit,
                found=round(age, 3),
            )
        else:
            _add(
                report,
                "FAIL",
                "manifest.max_age_days",
                f"captured {age:.1f} days ago; the policy allows at most {limit:g}",
                limit=limit,
                found=round(age, 3),
            )

    expected = policy.get("manifest", "require_exit_code")
    if expected is not None:
        found = manifest["execution"]["exit_code"]
        _add(
            report,
            "PASS" if found == expected else "FAIL",
            "manifest.require_exit_code",
            f"the recorded run exited with {found}"
            + ("" if found == expected else f"; the policy requires {expected}"),
            limit=expected,
            found=found,
        )

    git = manifest.get("git")
    if policy.get("manifest", "require_git"):
        _add(
            report,
            "PASS" if git else "FAIL",
            "manifest.require_git",
            f"Git commit {git['commit'][:12]} recorded" if git else "no Git state was recorded",
        )
    if policy.get("manifest", "forbid_dirty_git"):
        if git is None:
            _add(
                report,
                "INCONCLUSIVE",
                "manifest.forbid_dirty_git",
                "no Git state was recorded, so a clean working tree cannot be confirmed",
            )
        elif git["dirty"]:
            _add(
                report,
                "FAIL",
                "manifest.forbid_dirty_git",
                "the Git working tree had uncommitted changes when the evidence was captured",
            )
        else:
            _add(report, "PASS", "manifest.forbid_dirty_git", "the Git working tree was clean")

    if policy.get("manifest", "require_signature"):
        signature = report.signature
        if signature is None:
            _add(
                report,
                "INCONCLUSIVE",
                "manifest.require_signature",
                "a signature is required, but no --allowed-signers file was given to check it",
            )
        elif signature["status"] == "good":
            _add(
                report,
                "PASS",
                "manifest.require_signature",
                f"signed by {signature['principal']!r}",
            )
        elif signature["status"] == "bad":
            _add(
                report,
                "FAIL",
                "manifest.require_signature",
                "the signature is not good (see the 'manifest signature' check)",
            )
        else:
            _add(
                report,
                "INCONCLUSIVE",
                "manifest.require_signature",
                "the signature could not be checked (see the 'manifest signature' check)",
            )

    minimum = policy.get("manifest", "min_claims")
    if minimum is not None:
        count = len(manifest.get("claims") or [])
        _add(
            report,
            "PASS" if count >= minimum else "FAIL",
            "manifest.min_claims",
            f"{count} claim(s) recorded"
            + ("" if count >= minimum else f"; the policy requires at least {minimum}"),
            limit=minimum,
            found=count,
        )

    outputs = {o["path"]: o for o in manifest["outputs"]}
    for path in policy.get("outputs", "require_numeric") or []:
        item = outputs.get(path)
        if item is None:
            _add(
                report,
                "FAIL",
                "outputs.require_numeric",
                f"{path} is not a declared output",
                path=path,
            )
        elif item["compare"]["mode"] != "numeric":
            _add(
                report,
                "FAIL",
                "outputs.require_numeric",
                f"{path} is compared by exact hash, not with a tolerance",
                path=path,
            )
        else:
            _add(
                report,
                "PASS",
                "outputs.require_numeric",
                f"{path} is compared with a tolerance",
                path=path,
            )

    numeric = [o for o in manifest["outputs"] if o["compare"]["mode"] == "numeric"]
    for key, field_name in (("max_rtol", "rtol"), ("max_atol", "atol")):
        ceiling = policy.get("outputs", key)
        if ceiling is None:
            continue
        too_loose = [
            f"{o['path']} ({field_name}={o['compare'][field_name]:g})"
            for o in numeric
            if o["compare"][field_name] > ceiling
        ]
        rule = f"outputs.{key}"
        if too_loose:
            _add(
                report,
                "FAIL",
                rule,
                f"looser than {ceiling:g}: " + ", ".join(too_loose),
                limit=ceiling,
            )
        elif numeric:
            _add(
                report,
                "PASS",
                rule,
                f"all {len(numeric)} numeric output(s) have {field_name} <= {ceiling:g}",
                limit=ceiling,
            )
        else:
            _add(
                report,
                "PASS",
                rule,
                "no output is compared with a tolerance, so there is nothing to bound",
                limit=ceiling,
            )

    if policy.get("rerun", "require"):
        if report.rerun_executed:
            _add(report, "PASS", "rerun.require", "the recorded command was re-executed")
        elif not report.rerun_requested:
            _add(
                report,
                "INCONCLUSIVE",
                "rerun.require",
                "the policy requires a rerun, but this verification ran without --rerun",
            )
        else:
            _add(
                report,
                "INCONCLUSIVE",
                "rerun.require",
                "a rerun was requested but did not happen (see the other checks)",
            )
    if policy.get("rerun", "require_clean_env"):
        if not report.rerun_executed:
            _add(
                report,
                "INCONCLUSIVE",
                "rerun.require_clean_env",
                "there was no rerun, so the environment it ran in is unknown",
            )
        elif report.clean_env:
            _add(
                report,
                "PASS",
                "rerun.require_clean_env",
                "the rerun got a minimal environment (--clean-env)",
            )
        else:
            _add(
                report,
                "FAIL",
                "rerun.require_clean_env",
                "the rerun got the caller's whole environment; the policy requires --clean-env",
            )
