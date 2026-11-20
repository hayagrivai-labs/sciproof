"""Check examples/completions/sciproof.bash against the CLI by running real Bash completion."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import pytest

from sciproof.cli import _build_parser

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "completions" / "sciproof.bash"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs Bash")


def _subcommands():
    parser = _build_parser()
    return next(a.choices for a in parser._actions if isinstance(a, argparse._SubParsersAction))


def _options(parser):
    return {
        option
        for action in parser._actions
        if action.option_strings and action.help != argparse.SUPPRESS
        for option in action.option_strings
    }


def _complete(*words):
    """Return what the script offers for a command line whose last word is being completed."""
    driver = (
        'source "$1"; shift\n'
        'COMP_WORDS=("$@"); COMP_CWORD=$(($# - 1)); _sciproof\n'
        'printf "%s\\n" "${COMPREPLY[@]}"\n'
    )
    result = subprocess.run(
        [shutil.which("bash"), "-c", driver, "bash", SCRIPT.as_posix(), "sciproof", *words],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


def test_script_registers_a_completion_function():
    result = subprocess.run(
        [
            shutil.which("bash"),
            "-c",
            'source "$1"; complete -p sciproof',
            "bash",
            SCRIPT.as_posix(),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[-2:] == ["_sciproof", "sciproof"]


def test_completes_every_subcommand():
    assert set(_complete("")) == set(_subcommands())


def test_completes_every_global_option():
    assert set(_complete("-")) == _options(_build_parser())


@pytest.mark.parametrize("command, parser", _subcommands().items())
def test_completes_every_option_of_each_subcommand(command, parser):
    assert set(_complete(command, "-")) == _options(parser)


@pytest.mark.parametrize("command, parser", _subcommands().items())
def test_completes_the_choices_of_each_option(command, parser):
    for action in parser._actions:
        if action.choices is None or not action.option_strings:
            continue
        assert set(_complete(command, action.option_strings[-1], "")) == set(action.choices)


def test_filters_by_prefix_and_handles_joined_values():
    assert sorted(_complete("ca")) == ["calibrate", "capture"]
    assert _complete("-q", "ver") == ["verify"]
    assert _complete("verify", "--fo") == ["--format"]
    assert sorted(_complete("verify", "--format", "j")) == ["json", "junit"]
    assert _complete("verify", "--format", "=", "ht") == ["html"]
    assert _complete("pipeline", "") == ["verify"]


def test_skips_values_of_multi_value_options_and_the_command_line():
    assert "ro-crate" in _complete("export", "--value", "a", "b", "c", "--format", "")
    assert _complete("capture", "--claim", "x", "y", "--for") == ["--force"]
    assert _complete("capture", "--name", "n", "--", "python", "--") == []
    assert _complete("capture", "--name", "n", "python", "--") == []
