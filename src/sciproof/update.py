"""Update a baseline: capture the same experiment again and show what changed.

When an intended change makes the recorded evidence out of date (a new dataset, a better model),
``sciproof update`` repeats the capture with exactly the declarations that are in the manifest:
the same command, inputs, outputs, tolerances, claims, note and hardware record. It then compares
the old manifest with the new one, so the person who updates a baseline sees what moved.

The recorded command is executed, exactly as with ``verify --rerun``, so the same confirmation is
needed. The declared outputs are removed before the command runs (and restored if the capture
fails): otherwise a command that never wrote its outputs would turn the old files into the new
baseline.

A signature of the old manifest no longer matches the new one; it is reported, and the signature
file is left for you to replace with ``sciproof sign``.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sciproof.capture import (
    DEFAULT_ATOL,
    DEFAULT_MAX_LOG_BYTES,
    DEFAULT_RTOL,
    CaptureResult,
    capture,
)
from sciproof.claims import claim_expression
from sciproof.diffmanifests import DiffReport, diff_manifests
from sciproof.errors import ConfirmationRequired, UsageError
from sciproof.manifest import MANIFEST_SUFFIX, load_manifest, snapshot_dir_name
from sciproof.signing import signature_path
from sciproof.textutil import safe_text


@dataclass
class UpdateResult:
    """What an update did: the new capture, and how it differs from the old manifest."""

    capture: CaptureResult
    diff: DiffReport
    warnings: list[str] = field(default_factory=list)


@dataclass
class Declarations:
    """The arguments of the capture that produced a manifest, read back from it."""

    name: str
    argv: list[str]
    inputs: list[str]
    outputs: list[str]
    numeric_outputs: list[str]
    rtol: float
    atol: float
    claims: list[tuple[str, str]]
    note: str | None
    record_hardware: bool
    max_log_bytes: int = DEFAULT_MAX_LOG_BYTES


def log_limit_of(manifest: dict[str, Any]) -> int:
    """The ``--max-log-bytes`` the capture used, read back from the recorded streams.

    A stream that was cut shows the limit directly: the text that was kept is exactly that long
    (a multi-byte character cut in half is stored as U+FFFD, so this is exact for ASCII output and
    a close estimate otherwise). If nothing was cut the limit was at least as large as the longest
    stream, so the default or that size is enough to give the same result.
    """
    streams = [manifest["execution"]["stdout"], manifest["execution"]["stderr"]]
    cut = [len(s["text"].encode("utf-8")) for s in streams if s["truncated"]]
    if cut:
        return max(cut)
    return max([DEFAULT_MAX_LOG_BYTES, *(s["size"] for s in streams)])


def declarations_of(manifest: dict[str, Any]) -> Declarations:
    """Read the capture arguments back from a manifest. Raises ``UsageError`` if they differ per
    output in a way ``capture`` cannot express (it takes one tolerance for all numeric outputs)."""
    numeric = [o for o in manifest["outputs"] if o["compare"]["mode"] == "numeric"]
    tolerances = {(o["compare"]["rtol"], o["compare"]["atol"]) for o in numeric}
    if len(tolerances) > 1:
        raise UsageError(
            "the numeric outputs of this manifest have different tolerances, which `capture` "
            "cannot express; capture it again by hand"
        )
    rtol, atol = next(iter(tolerances)) if tolerances else (None, None)
    return Declarations(
        name=manifest["name"],
        argv=list(manifest["command"]["argv"]),
        inputs=[i["path"] for i in manifest["inputs"]],
        outputs=[o["path"] for o in manifest["outputs"] if o["compare"]["mode"] == "exact"],
        numeric_outputs=[o["path"] for o in numeric],
        rtol=DEFAULT_RTOL if rtol is None else rtol,
        atol=DEFAULT_ATOL if atol is None else atol,
        claims=[(c["output"], claim_expression(c)) for c in manifest.get("claims") or []],
        note=manifest.get("note"),
        record_hardware=bool(manifest["environment"].get("hardware")),
        max_log_bytes=log_limit_of(manifest),
    )


def describe_update(manifest_path: Path, declarations: Declarations) -> str:
    """What an update would do, for the confirmation prompt and ``--dry-run``."""
    command = " ".join(safe_text(part, 200) for part in declarations.argv)
    outputs = declarations.outputs + declarations.numeric_outputs
    return (
        "SciProof is about to EXECUTE the command recorded in this manifest and replace the "
        "evidence with the result:\n\n"
        f"    {command}\n\n"
        f"    working directory: {manifest_path.parent}\n"
        f"    replaces: {manifest_path.name} and {snapshot_dir_name(declarations.name)}/\n"
        f"    outputs removed first (restored if the capture fails): "
        f"{', '.join(map(safe_text, outputs)) or '(none)'}\n\n"
        "It will run with your current user's permissions and can read, change or delete anything "
        "you can, with all of your environment variables. A manifest is untrusted input: only "
        "continue if you trust its source and have read the command above."
    )


def _backup_and_remove(base: Path, outputs: list[str], backup: Path) -> list[str]:
    """Move the existing outputs into ``backup``; returns the paths that were moved."""
    moved = []
    for relative in outputs:
        target = base / relative
        if target.exists():
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), destination)
            moved.append(relative)
    return moved


def _restore(base: Path, backup: Path, moved: list[str]) -> None:
    for relative in moved:
        target = base / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(backup / relative), target)


def update_baseline(
    manifest_path: Path | str,
    *,
    assume_yes: bool = False,
    confirm: Callable[[str], bool] | None = None,
    note: str | None = None,
    timeout: float | None = None,
) -> UpdateResult | None:
    """Capture the experiment of ``manifest_path`` again and replace the manifest.

    Returns ``None`` if the confirmation was declined (nothing was executed or changed). Raises
    ``ConfirmationRequired`` without ``assume_yes`` or a ``confirm`` callback.
    """
    manifest_path = Path(manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    declarations = declarations_of(manifest)
    if manifest_path.name != f"{declarations.name}{MANIFEST_SUFFIX}":
        raise UsageError(
            f"{manifest_path.name} is not named after its experiment "
            f"({declarations.name}{MANIFEST_SUFFIX}); rename it back before updating"
        )
    prompt = describe_update(manifest_path, declarations)
    if not assume_yes:
        if confirm is None:
            raise ConfirmationRequired(
                "refusing to execute the recorded command without confirmation; re-run with "
                "--yes after reading the command (see 'sciproof update --dry-run')"
            )
        if not confirm(prompt):
            return None

    base = manifest_path.parent
    outputs = declarations.outputs + declarations.numeric_outputs
    with tempfile.TemporaryDirectory(prefix="sciproof-update-") as temporary:
        scratch = Path(temporary)
        # a copy of the old evidence, to compare with afterwards
        old_dir = scratch / "old"
        old_dir.mkdir()
        shutil.copy2(manifest_path, old_dir / manifest_path.name)
        snapshots = base / snapshot_dir_name(declarations.name)
        if snapshots.is_dir():
            shutil.copytree(snapshots, old_dir / snapshots.name)
        backup = scratch / "outputs"
        moved = _backup_and_remove(base, outputs, backup)
        try:
            result = capture(
                name=declarations.name,
                argv=declarations.argv,
                base=base,
                inputs=declarations.inputs,
                outputs=declarations.outputs,
                numeric_outputs=declarations.numeric_outputs,
                rtol=declarations.rtol,
                atol=declarations.atol,
                timeout=timeout,
                max_log_bytes=declarations.max_log_bytes,
                force=True,
                claims=declarations.claims,
                note=note if note is not None else declarations.note,
                record_hardware=declarations.record_hardware,
            )
        except BaseException:
            _restore(base, backup, moved)
            raise
        diff = diff_manifests(old_dir / manifest_path.name, result.manifest_path)

    warnings = list(result.warnings)
    if signature_path(manifest_path).is_file():
        warnings.append(
            f"{signature_path(manifest_path).name} signs the OLD manifest and no longer matches; "
            "sign the new one with `sciproof sign`"
        )
    return UpdateResult(result, diff, warnings)
