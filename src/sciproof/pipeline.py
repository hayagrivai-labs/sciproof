"""Multi-step scientific pipeline DAG validation and execution.

In computational science, an experiment is often a multi-stage workflow:
    Data Prep -> Model Training -> Evaluation -> Plotting

A SciProof pipeline ties manifests together into a verifiable Directed Acyclic Graph (DAG),
checking that outputs of predecessor steps connect immutably to inputs of successor steps
(chain-of-custody), and verifying the end-to-end integrity of the research pipeline.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sciproof.errors import SciProofError, UsageError
from sciproof.manifest import load_manifest
from sciproof.paths import resolve_inside
from sciproof.verify import Status, verify


@dataclass
class StepResult:
    name: str
    manifest_path: Path
    status: Status
    reason: str
    violations: list[str] = field(default_factory=list)


@dataclass
class PipelineReport:
    name: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def status(self) -> Status:
        if any(s.status is Status.FAIL for s in self.steps):
            return Status.FAIL
        if any(s.status is Status.INCONCLUSIVE for s in self.steps):
            return Status.INCONCLUSIVE
        return Status.PASS

    def to_text(self) -> str:
        lines = [
            f"SciProof Pipeline: {self.name}",
            f"Overall Verdict: {self.status.value}",
            "",
            "Steps:",
        ]
        for step in self.steps:
            lines.append(f"  [{step.status.value}] {step.name} ({step.manifest_path.name})")
            lines.append(f"      reason: {step.reason}")
            for v in step.violations:
                lines.append(f"      chain-of-custody break: {v}")
        return "\n".join(lines)


def load_pipeline(path: Path | str) -> dict[str, Any]:
    """Parse a TOML pipeline configuration."""
    path = Path(path).resolve()
    if not path.is_file():
        raise UsageError(f"pipeline file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SciProofError(f"cannot read pipeline {path.name}: {exc}") from exc

    if "pipeline" not in data or "name" not in data["pipeline"]:
        raise UsageError(f"{path.name}: missing [pipeline] table with 'name'")
    if "steps" not in data or not isinstance(data["steps"], list) or not data["steps"]:
        raise UsageError(f"{path.name}: [[steps]] list cannot be empty")
    return data


def verify_pipeline(path: Path | str) -> PipelineReport:
    """Verify all steps in a pipeline DAG and verify cross-step chain-of-custody."""
    pipe_path = Path(path).resolve()
    data = load_pipeline(pipe_path)
    base = pipe_path.parent
    name = data["pipeline"]["name"]
    report = PipelineReport(name=name)

    step_outputs: dict[str, dict[str, str]] = {}  # step_name -> {relative_path: sha256}
    step_defs = {s["name"]: s for s in data["steps"] if "name" in s}

    for step in data["steps"]:
        s_name = step.get("name")
        m_file = step.get("manifest")
        if not s_name or not m_file:
            raise UsageError(f"each step must have 'name' and 'manifest' (got {step!r})")

        m_path = resolve_inside(base, m_file)
        manifest = load_manifest(m_path)

        violations: list[str] = []
        depends_on = step.get("depends_on", [])
        for dep_name in depends_on:
            if dep_name not in step_defs:
                raise UsageError(f"step {s_name!r} depends on unknown step {dep_name!r}")
            # Must precede the has-not-run check: the current step is not yet in
            # step_outputs, so a self-dependency would otherwise get the wrong message.
            if dep_name == s_name:
                raise UsageError(f"step {s_name!r} cannot depend on itself")
            # step_outputs only holds steps already processed in this loop, so a
            # missing key means a forward reference (or the back-edge of a cycle).
            if dep_name not in step_outputs:
                raise UsageError(
                    f"step {s_name!r} depends on {dep_name!r}, which has not run yet. "
                    "Steps must be ordered topologically or declare prerequisites "
                    "that appear before them."
                )
            dep_out = step_outputs[dep_name]
            # Check inputs of this step that were produced by dep_name
            for inp in manifest.get("inputs", []):
                ipath = inp["path"]
                if ipath in dep_out and inp.get("sha256") != dep_out[ipath]:
                    violations.append(
                        f"input {ipath!r} sha256 {inp.get('sha256')[:12]} does not match "
                        f"output from prerequisite step {dep_name!r} ({dep_out[ipath][:12]})"
                    )

        rep = verify(m_path)
        step_status = rep.status
        if violations:
            step_status = Status.FAIL

        # Record this step's outputs for downstream steps
        step_outputs[s_name] = {
            o["path"]: o.get("sha256", "")
            for o in manifest.get("outputs", [])
            if o.get("present", True) and o.get("sha256")
        }

        reason = (
            rep.reason
            if not violations
            else "chain-of-custody broken by modified intermediate files"
            + (f"; {rep.reason}" if rep.reason else "")
        )
        report.steps.append(
            StepResult(
                name=s_name,
                manifest_path=m_path,
                status=step_status,
                reason=reason,
                violations=violations,
            )
        )

    return report
