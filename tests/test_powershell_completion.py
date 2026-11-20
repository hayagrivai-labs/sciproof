"""Keep examples/completions/sciproof.ps1 in sync with the CLI and check how it completes."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sciproof.cli import _build_parser

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "completions" / "sciproof.ps1"


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


def _script_option_tables():
    """Map each subcommand to the option names listed for it in the script."""
    text = SCRIPT.read_text(encoding="utf-8")
    block = text.split("$script:SciproofOptions = @{", 1)[1].split("\n}\n", 1)[0]
    tables = {}
    for entry in re.split(r"\n    (?=')", "\n" + block)[1:]:
        name = re.match(r"'([^']+)'", entry).group(1)
        tables[name] = set(re.findall(r"'(-{1,2}[A-Za-z][\w-]*)'", entry))
    return tables


def test_script_lists_every_subcommand():
    text = SCRIPT.read_text(encoding="utf-8")
    block = text.split("$script:SciproofSubcommands = [ordered]@{", 1)[1].split("\n}\n", 1)[0]
    assert set(re.findall(r"^\s+'([^']+)'\s+=", block, flags=re.M)) == set(_subcommands())


@pytest.mark.parametrize("command, parser", _subcommands().items())
def test_script_lists_every_option_of_each_subcommand(command, parser):
    assert _script_option_tables()[command] == _options(parser)


def test_script_lists_every_global_option():
    text = SCRIPT.read_text(encoding="utf-8")
    block = text.split("$script:SciproofGlobalOptions = @{", 1)[1].split("\n}\n", 1)[0]
    assert set(re.findall(r"'(-{1,2}[A-Za-z][\w-]*)'", block)) == _options(_build_parser())


@pytest.mark.parametrize("command, parser", _subcommands().items())
def test_script_lists_the_choices_of_each_option(command, parser):
    text = SCRIPT.read_text(encoding="utf-8")
    block = text.split("$script:SciproofChoices = @{", 1)[1].split("\n}\n", 1)[0]
    for action in parser._actions:
        if action.choices is None or not action.option_strings:
            continue
        for choice in action.choices:
            pattern = rf"'{command}'\s+= @\{{.*'{re.escape(choice)}'"
            assert re.search(pattern, block), (command, choice)


def _powershell():
    return shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_powershell() is None, reason="needs PowerShell")
def test_powershell_completes_subcommands_options_and_choices(tmp_path):
    lines = [
        "sciproof ca",
        "sciproof -q ver",
        "sciproof verify --fo",
        "sciproof verify --format j",
        "sciproof verify --format=ht",
        "sciproof pipeline ",
        "sciproof export --value a b c --format ",
        "sciproof capture --claim x y --for",
        "sciproof capture --name n -- python --",
    ]
    driver = tmp_path / "driver.ps1"
    driver.write_text(
        f". '{SCRIPT}'\n"
        f"$lines = '{json.dumps(lines)}' | ConvertFrom-Json\n"
        "$out = [ordered]@{}\n"
        "foreach ($line in $lines) {\n"
        "    $r = TabExpansion2 -inputScript $line -cursorColumn $line.Length\n"
        "    $out[$line] = @($r.CompletionMatches | ForEach-Object { $_.CompletionText })\n"
        "}\n"
        "$out | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-File", str(driver)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    completions = json.loads(result.stdout)
    assert sorted(completions["sciproof ca"]) == ["calibrate", "capture"]
    assert completions["sciproof -q ver"] == ["verify"]
    assert completions["sciproof verify --fo"] == ["--format"]
    assert sorted(completions["sciproof verify --format j"]) == ["json", "junit"]
    assert completions["sciproof verify --format=ht"] == ["--format=html"]
    assert completions["sciproof pipeline "] == ["verify"]
    assert "ro-crate" in completions["sciproof export --value a b c --format "]
    assert completions["sciproof capture --claim x y --for"] == ["--force"]
    assert completions["sciproof capture --name n -- python --"] == []
