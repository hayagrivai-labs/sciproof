"""Check standalone completions through Fish's real noninteractive completion engine."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sciproof.cli import _build_parser

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "completions" / "sciproof.fish"
FISH = shutil.which("fish")
pytestmark = pytest.mark.skipif(FISH is None, reason="needs Fish")


def _subcommands():
    return next(
        action.choices
        for action in _build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )


def _options(parser):
    return {
        option
        for action in parser._actions
        if action.help != argparse.SUPPRESS
        for option in action.option_strings
    }


def _complete(tmp_path, line):
    driver = """
        complete -c sciproof -e
        complete -c sciproof -f
        if test -f $argv[1]
            source $argv[1]; or exit 1
        end
        complete -c fake_experiment -f -l child-option -d 'Child option'
        complete -C $argv[2]
    """
    result = subprocess.run(
        [FISH, "--no-config", "-c", driver, str(SCRIPT), line],
        cwd=tmp_path,
        env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path / ".config")},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return {entry.split("\t", 1)[0] for entry in result.stdout.splitlines()}


def test_completes_every_current_subcommand(tmp_path):
    assert _complete(tmp_path, "sciproof ") == set(_subcommands())


def test_completes_every_global_option(tmp_path):
    assert _complete(tmp_path, "sciproof -") == _options(_build_parser())


@pytest.mark.parametrize("command, parser", _subcommands().items())
def test_completes_every_subcommand_option(tmp_path, command, parser):
    assert _complete(tmp_path, f"sciproof {command} -") == _options(parser)


@pytest.mark.parametrize("command, parser", _subcommands().items())
def test_completes_option_choices(tmp_path, command, parser):
    for action in parser._actions:
        if action.choices is not None and action.option_strings:
            assert _complete(tmp_path, f"sciproof {command} {action.option_strings[-1]} ") == set(
                action.choices
            )


def test_prefixes_global_options_and_joined_values(tmp_path):
    assert _complete(tmp_path, "sciproof ca") == {"calibrate", "capture"}
    assert _complete(tmp_path, "sciproof --quiet ver") == {"verify"}
    assert _complete(tmp_path, "sciproof verify --fo") == {"--format"}
    assert _complete(tmp_path, "sciproof verify --format j") == {"json", "junit"}
    assert _complete(tmp_path, "sciproof verify --format=ht") == {"--format=html"}
    assert _complete(tmp_path, "sciproof pipeline ") == {"verify"}


def test_multi_value_options_and_command_names_as_values(tmp_path):
    assert _complete(tmp_path, "sciproof capture --name verify --for") == {"--force"}
    assert _complete(tmp_path, "sciproof capture --claim x y --for") == {"--force"}
    assert _complete(tmp_path, "sciproof capture --claim x --for") == set()
    assert "ro-crate" in _complete(tmp_path, "sciproof export --value a b c --format ")
    assert _complete(tmp_path, "sciproof capture --name=verify --for") == {"--force"}


def test_completes_paths_with_spaces_and_only_directories_for_list(tmp_path):
    (tmp_path / "trial file.sciproof.json").touch()
    (tmp_path / "trial runs").mkdir()
    assert "trial file.sciproof.json" in _complete(tmp_path, "sciproof inspect trial")
    assert "trial file.sciproof.json" in _complete(tmp_path, "sciproof capture --input trial")
    assert "trial file.sciproof.json" in _complete(tmp_path, "sciproof export --value name trial")
    directories = _complete(tmp_path, "sciproof list trial")
    assert directories == {"trial runs/"}


@pytest.mark.parametrize("command", ["capture", "record", "suggest-inputs"])
def test_delegates_remainder_completion_without_leaking_sciproof_options(tmp_path, command):
    options = "" if command == "suggest-inputs" else "--name trial "
    for separator in ("", "-- "):
        line = f"sciproof {command} {options}{separator}fake_experiment --ch"
        assert _complete(tmp_path, line) == {"--child-option"}
        assert _complete(tmp_path, line.replace("--ch", "--for")) == set()


def test_completion_never_executes_the_experiment(tmp_path):
    experiment = tmp_path / "experiment"
    experiment.write_text("#!/bin/sh\ntouch experiment-started\n", encoding="utf-8")
    experiment.chmod(0o755)
    _complete(tmp_path, f"sciproof capture --name trial -- {experiment} --")
    assert not (tmp_path / "experiment-started").exists()
    assert not (tmp_path / "trial.sciproof.json").exists()


@pytest.mark.parametrize("command, parser", _subcommands().items())
def test_consumes_the_declared_number_of_values_before_offering_options(tmp_path, command, parser):
    for action in parser._actions:
        if not action.option_strings or action.nargs == 0:
            continue
        arity = action.nargs if isinstance(action.nargs, int) else 1
        value = str(next(iter(action.choices))) if action.choices is not None else "1"
        values = " ".join([value] * arity)
        line = f"sciproof {command} {action.option_strings[-1]} {values} --he"
        assert _complete(tmp_path, line) == {"--help"}
