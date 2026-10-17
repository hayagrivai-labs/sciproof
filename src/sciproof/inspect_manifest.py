"""Readable summary of a manifest. This module never executes anything."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sciproof.manifest import load_manifest
from sciproof.textutil import safe_text

_PREVIEW_LINES = 8


def _stream_preview(stream: dict[str, Any]) -> list[str]:
    text = stream["text"]
    if not text.strip():
        return ["      (empty)"]
    lines = text.splitlines()
    shown = [f"      | {safe_text(line, 200)}" for line in lines[:_PREVIEW_LINES]]
    if len(lines) > _PREVIEW_LINES or stream["truncated"]:
        shown.append("      | ...")
    return shown


def describe(manifest: dict[str, Any]) -> str:
    env = manifest["environment"]
    execution = manifest["execution"]
    git = manifest.get("git")
    lines = [
        f"SciProof manifest: {safe_text(manifest['name'])}",
        f"  format:      {manifest['schema']} {manifest['schema_version']}",
        f"  captured:    {safe_text(manifest['created_at'])}",
        "",
        "Command (recorded, NOT executed by 'inspect'):",
        "  " + " ".join(safe_text(part, 200) for part in manifest["command"]["argv"]),
        "  working directory: the directory containing the manifest",
        "",
        "Environment:",
        f"  python:      {safe_text(env['python']['version'])} "
        f"({safe_text(env['python'].get('implementation', '?'))})",
        f"  platform:    {safe_text(env['platform'].get('platform', '?'))}",
        f"  recorded by: {safe_text(env.get('source', 'unknown'))}",
        f"  packages:    {len(manifest['dependencies'])} recorded",
    ]
    if env["env_vars"]:
        lines.append(
            "  variables:   "
            + ", ".join(
                f"{safe_text(k)}={safe_text(v, 80)}" for k, v in sorted(env["env_vars"].items())
            )
        )
    if git:
        state = "UNCOMMITTED CHANGES" if git["dirty"] else "clean"
        lines.append(f"  git:         {safe_text(git['commit'])[:12]} ({state})")
    else:
        lines.append("  git:         not recorded")

    lines += ["", f"Inputs ({len(manifest['inputs'])}):"]
    lines += [
        f"  {safe_text(i['path'])}  sha256:{i['sha256'][:16]}...  {_size_text(i)}"
        for i in manifest["inputs"]
    ] or ["  (none)"]

    lines += ["", "Execution:"]
    lines.append(f"  exit code:   {execution['exit_code']}")
    lines.append(f"  duration:    {execution['duration_seconds']} s")
    lines.append(f"  stdout:      {execution['stdout']['size']} bytes")
    lines += _stream_preview(execution["stdout"])
    lines.append(f"  stderr:      {execution['stderr']['size']} bytes")
    lines += _stream_preview(execution["stderr"])

    lines += ["", f"Outputs ({len(manifest['outputs'])}):"]
    for item in manifest["outputs"]:
        compare = item["compare"]
        if compare["mode"] == "numeric":
            how = f"numeric (rtol={compare['rtol']}, atol={compare['atol']})"
        else:
            how = "exact hash"
        if item["present"]:
            lines.append(
                f"  {safe_text(item['path'])}  sha256:{item['sha256'][:16]}...  "
                f"{_size_text(item)}  compare: {how}"
            )
        else:
            lines.append(f"  {safe_text(item['path'])}  (absent when captured)  compare: {how}")
    if not manifest["outputs"]:
        lines.append("  (none)")

    lines += [
        "",
        "Next: 'sciproof verify <manifest>' checks that the files still match this evidence.",
        "      Add --rerun only if you trust the command shown above.",
    ]
    return "\n".join(lines)


def _size_text(item: dict[str, Any]) -> str:
    if item.get("kind") == "directory":
        return f"directory, {len(item['files'])} files, {item['size']} bytes"
    return f"{item['size']} bytes"


def inspect_file(path: Path) -> str:
    return describe(load_manifest(path))
