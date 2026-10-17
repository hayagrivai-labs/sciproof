from __future__ import annotations

import sys

import pytest

from sciproof import environment
from sciproof.environment import _looks_like_python, interpreter_info


@pytest.mark.parametrize(
    "name",
    [
        "python",
        "python3",
        "python3.13",
        "python3.13t",
        "pythonw",
        "py",
        "pypy3",
        "PYTHON.EXE",
        "/usr/bin/python3",
        "venv/Scripts/python.exe",
    ],
)
def test_real_interpreter_names_are_recognised(name):
    assert _looks_like_python(name)


@pytest.mark.parametrize(
    "name",
    [
        "python_train.sh",
        "python-config",
        "python3-config",
        "pythonista",
        "./python_run",
        "pyfoo",
        "train.py",
        "echo",
        "",
    ],
)
def test_other_programs_are_not_probed(name):
    assert not _looks_like_python(name)


def test_a_script_named_like_python_is_never_executed(tmp_path, monkeypatch):
    script = tmp_path / "python_train.sh"
    script.write_text("#!/bin/sh\n")
    spawned = []

    def spy(args, *a, **kw):
        spawned.append(args)
        raise OSError("must not be called")

    monkeypatch.setattr(environment.subprocess, "run", spy)
    info, source = interpreter_info(str(script), tmp_path)
    assert spawned == []
    assert source == "sciproof-process"
    assert info["version"].count(".") == 2


def test_the_current_interpreter_is_still_probed(tmp_path):
    info, source = interpreter_info(sys.executable, tmp_path)
    assert source == "command-interpreter"
    assert info["version"].count(".") == 2
