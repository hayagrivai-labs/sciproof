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
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from sciproof.errors import UsageError

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

# What a command needs to start at all, passed by --clean-env. No home directory, no
# credentials, no proxy or cloud settings: those must be requested with --pass-env.
CLEAN_ENV_BASE = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TMPDIR", "TEMP", "TMP")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PROBE_TIMEOUT_SECONDS = 30
GPU_PROBE_TIMEOUT_SECONDS = 10
MAX_GPUS = 16
_GPU_QUERY = ("--query-gpu=name,driver_version,memory.total", "--format=csv,noheader,nounits")

# The source of the function that lists installed packages. One text, two uses: the child
# interpreter that runs the experiment executes it (it has no SciProof), and this process runs it
# through ``exec`` when it has to describe itself, so both always list packages the same way.
#
# ``importlib.metadata`` parses the METADATA file of every package just to learn its name, which
# takes about 2 ms per package. The directory of an installed package is named
# ``<name>-<version>.dist-info`` (or ``.egg-info``), which already says both; reading that is
# about 50 times faster. Anything that does not fit (a zipped egg, an unusual layout) is read the
# slow, exact way.
_LIST_PACKAGES_SOURCE = r"""
def list_packages():
    import importlib.metadata as md
    import re

    seen = {}
    for dist in md.distributions():
        path = getattr(dist, "_path", None)
        match = None
        if path is not None:
            match = re.fullmatch(r"(.+?)-([0-9][^-]*)\.(?:dist|egg)-info", path.name)
        if match:
            name, version = match.group(1), match.group(2)
        else:
            name, version = (dist.metadata["Name"] or "").strip(), dist.version
        if name:
            seen[re.sub(r"[-_.]+", "-", name).lower()] = [name, version]
    return sorted(seen.values(), key=lambda item: item[0].lower())
"""

# Executed by the interpreter that runs the experiment. It prints one JSON document.
_PROBE_SCRIPT = (
    "import json, platform, sys\n"
    + _LIST_PACKAGES_SOURCE
    + """
print(json.dumps({
    "version": platform.python_version(),
    "implementation": platform.python_implementation(),
    "byteorder": sys.byteorder,
    "packages": list_packages(),
}))
"""
)


def normalize_package_name(name: str) -> str:
    """The standard (PEP 503) normal form of a package name: ``Jaraco.Context`` and
    ``jaraco_context`` are the same package. Keep in step with the line in
    ``_LIST_PACKAGES_SOURCE``, which cannot import this module."""
    return re.sub(r"[-_.]+", "-", name).lower()


def list_packages() -> list[list[str]]:
    """``[name, version]`` of every installed package, sorted by name (see the note above)."""
    namespace: dict[str, Any] = {}
    exec(_LIST_PACKAGES_SOURCE, namespace)  # noqa: S102  (a constant defined in this module)
    packages: list[list[str]] = namespace["list_packages"]()
    return packages


def hardware_info() -> dict[str, Any]:
    """CPU count, processor and NVIDIA GPUs (opt-in: ``capture --record-hardware``).

    Hardware can change numerical results (thread counts, GPU kernels), so it is worth
    comparing. Only what is needed for that is recorded: no host name, user name, serial
    number or GPU UUID. GPUs are listed through ``nvidia-smi`` if it is installed; its
    absence is recorded as such, not as "no GPU".
    """
    gpus: list[dict[str, Any]] = []
    executable = shutil.which("nvidia-smi")
    probe = "nvidia-smi" if executable else "none"
    if executable:
        try:
            completed = subprocess.run(  # noqa: S603
                [executable, *_GPU_QUERY],
                capture_output=True,
                text=True,
                timeout=GPU_PROBE_TIMEOUT_SECONDS,
                check=True,
            )
            for line in completed.stdout.splitlines()[:MAX_GPUS]:
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 3 and parts[0]:
                    gpus.append(
                        {
                            "name": ",".join(parts[:-2])[:100],
                            "driver": parts[-2][:40],
                            "memory_mib": int(parts[-1]) if parts[-1].isdigit() else None,
                        }
                    )
        except (OSError, subprocess.SubprocessError):
            probe = "failed"
    return {
        "cpu_count": os.cpu_count(),
        "processor": platform.processor()[:200],
        "gpu_probe": probe,
        "gpus": gpus,
    }


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


def clean_environment(
    extra: Iterable[str] = (), environ: Mapping[str, str] | None = None
) -> tuple[dict[str, str], list[str]]:
    """Build a minimal environment for a rerun.

    Contains only the variables a program needs to start (``CLEAN_ENV_BASE``), the
    numerics-related allowlist (``ENV_ALLOWLIST``) and the variables named in ``extra``,
    each only if it is set. Returns ``(environment, requested_but_unset)``. Names are
    matched case-insensitively on Windows, where variable names are case-insensitive.
    """
    source = os.environ if environ is None else environ
    wanted = list(CLEAN_ENV_BASE) + list(ENV_ALLOWLIST)
    explicit = []
    for name in extra:
        if not _ENV_NAME.fullmatch(name):
            raise UsageError(f"{name!r} is not a valid environment variable name")
        explicit.append(name)
    wanted += explicit
    fold = (lambda text: text.upper()) if sys.platform == "win32" else (lambda text: text)
    available = {fold(key): key for key in source}
    result: dict[str, str] = {}
    for name in wanted:
        key = available.get(fold(name))
        if key is not None:
            result[key] = source[key]
    unset = [name for name in explicit if fold(name) not in available]
    return result, unset


def _own_interpreter_info() -> dict[str, Any]:
    return {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "byteorder": sys.byteorder,
        "packages": list_packages(),
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
