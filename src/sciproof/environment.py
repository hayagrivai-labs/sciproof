"""Collect environment metadata without leaking secrets.

Only an explicit allowlist of environment variables is ever recorded. Absolute paths
(interpreter location, home directory, user name) are deliberately left out.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess  # noqa: S404  (used to ask the recorded interpreter for its version)
import sys
from pathlib import Path
from typing import Any

# Variables that influence numerical or locale behaviour and are not secret.
ENV_ALLOWLIST = (
    "PYTHONHASHSEED",
    "TZ",
    "LANG",
    "LC_ALL",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "CUDA_VISIBLE_DEVICES",
)

PROBE_TIMEOUT_SECONDS = 30

# Executed by the interpreter that runs the experiment. It prints one JSON document.
_PROBE_SCRIPT = r"""
import importlib.metadata as md, json, platform, sys
seen = {}
for dist in md.distributions():
    name = (dist.metadata["Name"] or "").strip()
    if name:
        seen[name.lower().replace("_", "-")] = [name, dist.version]
print(json.dumps({
    "version": platform.python_version(),
    "implementation": platform.python_implementation(),
    "byteorder": sys.byteorder,
    "packages": sorted(seen.values(), key=lambda item: item[0].lower()),
}))
"""


def platform_info() -> dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "platform": platform.platform(),
    }


def selected_env_vars(environ: dict[str, str] | None = None) -> dict[str, str]:
    environ = os.environ if environ is None else environ
    return {name: environ[name] for name in ENV_ALLOWLIST if name in environ}


def _own_interpreter_info() -> dict[str, Any]:
    import importlib.metadata as md

    seen: dict[str, list[str]] = {}
    for dist in md.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name:
            seen[name.lower().replace("_", "-")] = [name, dist.version]
    return {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "byteorder": sys.byteorder,
        "packages": sorted(seen.values(), key=lambda item: item[0].lower()),
    }


_INTERPRETER_NAME = re.compile(r"^(py|pythonw?(\d+(\.\d+)*[dt]*)?|pypy(\d+(\.\d+)*)?)$")


def _looks_like_python(executable: str) -> bool:
    """True only for names of real interpreters (``python``, ``python3.13``, ``py``, ``pypy3``).

    The probe runs ``<executable> -c <snippet>``; running it on ``python_train.sh`` or
    ``python-config`` would execute the user's own program with unexpected arguments.
    """
    name = Path(executable).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return bool(_INTERPRETER_NAME.fullmatch(name))


def interpreter_info(argv0: str, cwd: Path) -> tuple[dict[str, Any], str]:
    """Describe the Python interpreter that runs the experiment.

    If ``argv0`` is a Python interpreter, it is asked for its own version and installed
    packages (this runs a fixed, built-in snippet and not the experiment). Otherwise the
    interpreter that runs SciProof is described and the source is reported as such.

    Returns ``(info, source)`` where source is ``"command-interpreter"`` or
    ``"sciproof-process"``.
    """
    resolved = shutil.which(argv0, path=None) if _looks_like_python(argv0) else None
    if resolved is None and _looks_like_python(argv0) and Path(argv0).exists():
        resolved = argv0
    if resolved is not None:
        try:
            completed = subprocess.run(  # noqa: S603
                [resolved, "-c", _PROBE_SCRIPT],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=True,
            )
            info = json.loads(completed.stdout)
            return info, "command-interpreter"
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    return _own_interpreter_info(), "sciproof-process"
