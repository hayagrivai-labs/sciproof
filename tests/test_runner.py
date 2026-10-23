"""``run_command``: starting tools the way a terminal finds them."""

from __future__ import annotations

import os
import sys

import pytest

from sciproof.runner import CommandNotRunnable, _resolve_batch_tool, run_command

windows_only = pytest.mark.skipif(sys.platform != "win32", reason=".cmd lookup is Windows-only")


def _tool(directory, name="othertool.cmd", text="@echo off\r\necho from-%~n0\r\n"):
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_bytes(text.encode())
    return path


def _env(directory):
    return {**os.environ, "PATH": f"{directory}{os.pathsep}{os.environ['PATH']}"}


@windows_only
def test_a_cmd_tool_on_path_is_started_by_its_bare_name(tmp_path):
    _tool(tmp_path / "bin")
    result = run_command(["othertool"], tmp_path, 30, _env(tmp_path / "bin"))
    assert result.exit_code == 0
    assert b"from-othertool" in result.stdout


@windows_only
def test_a_cmd_tool_is_started_by_its_name_with_extension(tmp_path):
    _tool(tmp_path / "bin")
    result = run_command(["othertool.cmd"], tmp_path, 30, _env(tmp_path / "bin"))
    assert b"from-othertool" in result.stdout


@windows_only
def test_the_project_directory_is_not_searched_for_a_bare_name(tmp_path):
    _tool(tmp_path, "localtool.cmd")  # in the working directory, not on PATH
    assert _resolve_batch_tool("localtool", {"PATH": str(tmp_path / "empty")}) is None
    with pytest.raises(CommandNotRunnable):
        run_command(["localtool"], tmp_path, 30, {**os.environ, "PATH": str(tmp_path / "empty")})


@windows_only
def test_an_executable_earlier_on_path_wins_over_a_later_cmd(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    (first / "othertool.exe").write_bytes(b"")
    _tool(second)
    env = {**os.environ, "PATH": f"{first}{os.pathsep}{second}"}
    assert _resolve_batch_tool("othertool", env) is None


@windows_only
def test_a_path_with_a_separator_is_left_alone(tmp_path):
    _tool(tmp_path / "bin")
    assert _resolve_batch_tool("bin/othertool", _env(tmp_path / "bin")) is None


def test_other_platforms_never_rewrite_the_command(tmp_path):
    if sys.platform == "win32":
        pytest.skip("covered by the Windows tests")
    assert _resolve_batch_tool("othertool", _env(tmp_path)) is None


def test_a_missing_command_is_still_reported(tmp_path):
    with pytest.raises(CommandNotRunnable, match="nonexistent-tool-xyz"):
        run_command(["nonexistent-tool-xyz"], tmp_path, 30)
