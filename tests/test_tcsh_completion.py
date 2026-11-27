"""Keep examples/completions/sciproof.tcsh in sync with the CLI and check that Tcsh loads it."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sciproof.cli import _build_parser

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "completions" / "sciproof.tcsh"

# Subcommands whose last argument is a command line (argparse.REMAINDER). The script and the
# docs describe how they are completed; a new one needs those descriptions reviewed.
_REMAINDER_COMMANDS = {"capture", "record", "suggest-inputs"}


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


def _subcommand_options(*, long: bool) -> set[str]:
    """The long (or the short) options of all subcommands, with their aliases."""
    return {
        option
        for parser in _subcommands().values()
        for option in _options(parser)
        if option.startswith("--") == long
    }


def _format_choices() -> set[str]:
    return {
        choice
        for parser in _subcommands().values()
        for action in parser._actions
        if "--format" in action.option_strings
        for choice in action.choices
    }


def _rules() -> list[str]:
    """Return the quoted rules of the complete command, without the comment lines."""
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    return re.findall(r"'([pcCnN]/[^']*/)'", code)


def _word_list(kind: str, pattern: str) -> set[str]:
    """Return the words of the rule kind/pattern/(...)/ (there must be exactly one)."""
    prefix = f"{kind}/{pattern}/("
    found = [rule for rule in _rules() if rule.startswith(prefix)]
    assert len(found) == 1, f"expected exactly one {prefix}...)/ rule"
    return set(found[0][len(prefix) : -2].split())


def _index(prefix: str) -> int:
    return next(i for i, rule in enumerate(_rules()) if rule.startswith(prefix))


def test_script_uses_unix_line_endings():
    assert b"\r" not in SCRIPT.read_bytes()


def test_script_has_no_duplicate_rules():
    rules = _rules()
    assert rules, "No Tcsh completion rules found"
    keys = [tuple(rule.split("/")[:2]) for rule in rules]
    assert len(keys) == len(set(keys))


def test_script_uses_only_the_documented_rule_types():
    # p: position, C: current word that keeps its prefix, n: next word. The lowercase c is
    # not used, because its lists would not hold whole words.
    assert {rule[0] for rule in _rules()} == {"p", "C", "n"}


def test_script_rules_are_in_the_order_tcsh_needs():
    rules = _rules()
    assert rules[0].startswith("p/1/(")
    current_word = [i for i, rule in enumerate(rules) if rule[0] == "C"]
    next_word = [i for i, rule in enumerate(rules) if rule[0] == "n"]
    assert max(current_word) < min(next_word)
    assert _index("C/--format=/(") < _index("C/--/(") < _index("C/-/(")
    assert _index("n/--format=/") < _index("n/--format/(")


def test_script_lists_every_subcommand_and_global_option():
    assert _word_list("p", "1") == set(_subcommands()) | _options(_build_parser())


def test_script_lists_every_long_option_and_alias():
    assert _word_list("C", "--") == _subcommand_options(long=True)


def test_script_lists_every_short_option():
    # The extra "--" lets "-" Tab reach the long options.
    assert _word_list("C", "-") == _subcommand_options(long=False) | {"--"}


def test_format_is_the_only_option_with_fixed_choices():
    with_choices = {
        option
        for parser in _subcommands().values()
        for action in parser._actions
        if action.option_strings and action.choices
        for option in action.option_strings
    }
    assert with_choices == {"--format"}


def test_script_lists_the_format_choices_of_all_subcommands():
    choices = _format_choices()
    assert _word_list("n", "--format") == choices
    assert _word_list("C", "--format=") == {f"--format={choice}" for choice in choices}


def test_script_lists_the_pipeline_actions():
    pipeline = _subcommands()["pipeline"]
    expected = {
        choice
        for action in pipeline._actions
        if not action.option_strings and action.choices
        for choice in action.choices
    }
    assert _word_list("n", "pipeline") == expected


def test_script_leaves_paths_and_other_values_to_file_name_completion():
    next_word_rules = [rule for rule in _rules() if rule[0] == "n"]
    patterns = [rule.split("/")[1] for rule in next_word_rules]
    assert patterns == ["--format=", "--format", "pipeline"]
    assert "n/--format=/f/" in next_word_rules
    # Static rules cannot tell the "--" separator from an option: no command-name completion.
    assert not any(rule.endswith("/c/") for rule in _rules())


def test_remainder_commands_are_the_ones_the_script_describes():
    remainder = {
        name
        for name, parser in _subcommands().items()
        if any(action.nargs == argparse.REMAINDER for action in parser._actions)
    }
    assert remainder == _REMAINDER_COMMANDS


def _run_tcsh(command: str) -> subprocess.CompletedProcess[str]:
    """Run a Tcsh command line; the script path is available to it as $argv[1]."""
    return subprocess.run(
        [shutil.which("tcsh"), "-f", "-c", command, SCRIPT.as_posix()],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.skipif(shutil.which("tcsh") is None, reason="needs Tcsh")
def test_tcsh_loads_the_script_quietly():
    result = _run_tcsh("source $argv[1]; source $argv[1]")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.skipif(shutil.which("tcsh") is None, reason="needs Tcsh")
def test_tcsh_reports_every_rule_of_the_script():
    result = _run_tcsh("source $argv[1]; complete sciproof")
    assert result.returncode == 0, result.stderr
    printed = " ".join(result.stdout.split())
    for rule in _rules():
        assert rule in printed, rule
