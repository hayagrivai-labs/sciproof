"""Run a command without a shell and collect its output."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess  # noqa: S404
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sciproof.errors import SciProofError
from sciproof.hashing import sha256_bytes


class CommandNotRunnable(SciProofError):
    """The command could not be started (not found, not executable, bad working directory)."""


class CommandTimeout(SciProofError):
    """The command did not finish within the allowed time."""


class CommandInterrupted(SciProofError):
    """The user interrupted the command (Ctrl+C)."""


@dataclass
class ExecResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float


_DRAIN_SECONDS = 5.0


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill the command and everything it started (best effort)."""
    try:
        if sys.platform == "win32":
            subprocess.run(  # noqa: S603
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],  # noqa: S607
                capture_output=True,
                check=False,
                timeout=30,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    with contextlib.suppress(OSError):
        process.kill()


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Kill the tree, then collect what it printed without waiting for stray children."""
    _kill_process_tree(process)
    try:
        process.communicate(timeout=_DRAIN_SECONDS)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        process.wait()


_BATCH_SUFFIXES = (".cmd", ".bat")


def _resolve_batch_tool(name: str, env: Mapping[str, str] | None) -> str | None:
    """On Windows, the ``.cmd`` or ``.bat`` file that a bare command name stands for, or ``None``.

    ``CreateProcess`` only appends ``.exe`` to a bare name, so ``npm`` or ``mvn`` (``npm.cmd``,
    ``mvn.cmd``) cannot be started although a terminal finds them. The lookup uses the ``PATH`` and
    ``PATHEXT`` of the environment the command will get, never the project directory (a file in
    an untrusted project must not be picked for a bare name). Anything else is left to the system.
    """
    if sys.platform != "win32" or any(char in name for char in "/\\:"):
        return None
    source = os.environ if env is None else env
    lookup = {key.upper(): value for key, value in source.items()}
    extensions = [e.lower() for e in lookup.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    suffix = Path(name).suffix.lower()
    candidates = [name] if suffix in extensions else [name + e for e in extensions]
    for directory in filter(None, lookup.get("PATH", "").split(os.pathsep)):
        for candidate in candidates:
            path = Path(directory) / candidate
            if path.is_file():
                # a real executable that comes first on PATH is left to the system
                return str(path) if path.suffix.lower() in _BATCH_SUFFIXES else None
    return None


def run_command(
    argv: list[str],
    cwd: Path,
    timeout: float | None,
    env: Mapping[str, str] | None = None,
) -> ExecResult:
    """Run ``argv`` (never through a shell) in ``cwd`` with the current user's permissions.

    ``env`` is the complete environment of the command; ``None`` means a copy of the
    environment of this process.

    Standard input is closed so that the command can never wait for terminal input. The command
    runs in its own process group (session on POSIX), so a timeout or Ctrl+C stops everything it
    started and not only the first process.
    """
    started = time.monotonic()
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    launch = list(argv)
    batch_tool = _resolve_batch_tool(launch[0], env)
    if batch_tool is not None:
        launch[0] = batch_tool
    try:
        process = subprocess.Popen(  # noqa: S603
            launch,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            env=None if env is None else dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **options,
        )
    except OSError as exc:
        raise CommandNotRunnable(f"cannot start {argv[0]!r}: {exc.strerror or exc}") from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _stop(process)
        raise CommandTimeout(f"command did not finish within {timeout} seconds") from exc
    except KeyboardInterrupt as exc:
        _stop(process)
        raise CommandInterrupted("interrupted by the user") from exc
    return ExecResult(
        exit_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=time.monotonic() - started,
    )


def summarize_stream(data: bytes, max_bytes: int) -> dict[str, Any]:
    """Describe captured output: hash of everything, text of (at most) the first ``max_bytes``."""
    kept = data[:max_bytes]
    return {
        "sha256": sha256_bytes(data),
        "size": len(data),
        "truncated": len(data) > max_bytes,
        "text": kept.decode("utf-8", errors="replace"),
    }
