"""Run Zsh's completion widget in a terminal, without executing the command line."""

from __future__ import annotations

import argparse
import os
import select
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from sciproof.cli import _build_parser

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "completions" / "_sciproof"
pytestmark = pytest.mark.skipif(
    shutil.which("zsh") is None or not hasattr(os, "openpty"), reason="needs Zsh and a PTY"
)


def _subcommands():
    parser = _build_parser()
    return next(a.choices for a in parser._actions if isinstance(a, argparse._SubParsersAction))


def _options(parser):
    return {
        option
        for action in parser._actions
        if action.help != argparse.SUPPRESS
        for option in action.option_strings
    }


def _complete(tmp_path, line):
    # openpty is unavailable on Windows; the module's Zsh skip applies there.
    master, slave = os.openpty()
    output = tmp_path / ".matches"
    env = {**os.environ, "TERM": "dumb", "ZDOTDIR": str(tmp_path), "MATCHES": str(output)}
    process = subprocess.Popen(
        [shutil.which("zsh"), "-f", "-i"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=tmp_path,
        env=env,
    )
    os.close(slave)
    transcript = bytearray()

    def wait_for(marker):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                transcript.extend(os.read(master, 65536))
                if marker in transcript:
                    return
        pytest.fail(f"Zsh did not finish completion: {transcript.decode(errors='replace')}")

    # Keep the real compadd call: _arguments relies on its result for completion state.
    # -O observes the matches selected by Zsh, including prefix and path filtering.
    setup = r"""
        fpath=("$1" $fpath)
        autoload -Uz compinit
        compinit -u -D
        compadd() {
            local -a observed_matches
            builtin compadd -O observed_matches "$@"
            print -rl -- "${observed_matches[@]}" >> "$MATCHES"
            builtin compadd "$@"
        }
        _capture() {
            : > "$MATCHES"
            zle complete-word
            BUFFER=''
            print -r -- COMPLETION_DONE
            zle reset-prompt
        }
        zle -N _capture
        bindkey '^X' _capture
        print -r -- COMPLETION_READY
    """
    # Pass the directory as a positional parameter rather than interpolating shell paths.
    setup = "set -- " + shlex.quote(str(SCRIPT.parent)) + "\n" + setup
    try:
        os.write(master, setup.encode())
        wait_for(b"\r\nCOMPLETION_READY\r\n")
        transcript.clear()
        os.write(master, line.encode() + b"\x18")
        wait_for(b"COMPLETION_DONE")
        return set(output.read_text(encoding="utf-8").splitlines()) - {""}
    finally:
        process.kill()
        process.wait(timeout=5)
        os.close(master)


def test_completes_every_subcommand(tmp_path):
    assert _complete(tmp_path, "sciproof ") == set(_subcommands())


def test_completes_global_options(tmp_path):
    assert _complete(tmp_path, "sciproof -") == _options(_build_parser())


@pytest.mark.parametrize("command, parser", _subcommands().items())
def test_completes_each_subcommand_options(tmp_path, command, parser):
    matches = _complete(tmp_path, f"sciproof {command} -")
    if any(action.nargs == argparse.REMAINDER for action in parser._actions):
        # Zsh's normal command completion also offers the command prefix "-".
        matches.discard("-")
    assert matches == _options(parser)


@pytest.mark.parametrize("command, parser", _subcommands().items())
def test_completes_option_choices(tmp_path, command, parser):
    for action in parser._actions:
        if action.choices is not None and action.option_strings:
            assert _complete(tmp_path, f"sciproof {command} {action.option_strings[-1]} ") == set(
                action.choices
            )


def test_prefixes_joined_values_and_pipeline_action(tmp_path):
    assert _complete(tmp_path, "sciproof ca") == {"calibrate", "capture"}
    assert _complete(tmp_path, "sciproof -q ver") == {"verify"}
    assert _complete(tmp_path, "sciproof verify --format=j") == {"json", "junit"}
    assert _complete(tmp_path, "sciproof pipeline ") == {"verify"}


def test_completes_paths_and_directories(tmp_path):
    (tmp_path / "trial.sciproof.json").touch()
    (tmp_path / "trial runs").mkdir()
    assert "trial.sciproof.json" in _complete(tmp_path, "sciproof inspect trial")
    assert "trial.sciproof.json" in _complete(tmp_path, "sciproof capture --input trial")
    matches = _complete(tmp_path, "sciproof list trial")
    assert any("trial" in match and "runs" in match for match in matches)
    assert "trial.sciproof.json" not in matches


def test_multi_value_options_and_command_delegation(tmp_path):
    assert _complete(tmp_path, "sciproof capture --claim x y --for") == {"--force"}
    assert "ro-crate" in _complete(tmp_path, "sciproof export --value a b c --format ")
    assert _complete(tmp_path, "sciproof capture --name trial -- echo --for") == set()
    assert _complete(tmp_path, "sciproof record --name trial echo --for") == set()
    assert not (tmp_path / "trial.sciproof.json").exists()
