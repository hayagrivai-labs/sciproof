from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from sciproof import gitinfo
from sciproof.gitinfo import _is_dirty, git_info

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
NUL = chr(0)  # git status -z separates entries with NUL


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch, tmp_path_factory):
    config = tmp_path_factory.mktemp("git-config")
    empty = config / "empty"
    empty.write_text("")
    hooks = config / "hooks"
    hooks.mkdir()
    templates = config / "templates"
    templates.mkdir()
    # No personal config, hooks, templates or commit signing in synthetic repositories.
    for name in list(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    for name, value in {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": str(empty),
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_TEMPLATE_DIR": str(templates),
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(hooks),
        "GIT_CONFIG_KEY_1": "commit.gpgsign",
        "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_KEY_2": "tag.gpgsign",
        "GIT_CONFIG_VALUE_2": "false",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "code.py").write_text("print(1)")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


@needs_git
def test_clean_tree_is_not_dirty(repo):
    assert git_info(repo)["dirty"] is False


@needs_git
def test_sciproof_files_do_not_make_the_tree_dirty(repo):
    (repo / "exp.sciproof.json").write_text("{}")
    (repo / "exp.sciproof.d").mkdir()
    (repo / "exp.sciproof.d" / "0-out.csv").write_text("1")
    rerun = repo / ".sciproof-rerun-20260101T000000Z" / "out"
    rerun.mkdir(parents=True)
    (rerun / "r.txt").write_text("x")
    assert git_info(repo)["dirty"] is False


@needs_git
def test_other_changes_still_make_the_tree_dirty(repo):
    (repo / "exp.sciproof.json").write_text("{}")
    (repo / "notes.txt").write_text("untracked")
    assert git_info(repo)["dirty"] is True


@needs_git
def test_modified_tracked_file_makes_the_tree_dirty(repo):
    (repo / "code.py").write_text("print(2)")
    assert git_info(repo)["dirty"] is True


@needs_git
def test_file_names_with_spaces_and_unicode_are_handled(repo):
    (repo / ("odd name " + chr(233) + " file.txt")).write_text("x")
    assert git_info(repo)["dirty"] is True


def test_unreadable_status_is_reported_as_dirty():
    assert _is_dirty(None) is True
    assert _is_dirty("") is False
    assert _is_dirty(f"?? a.sciproof.json{NUL}") is False
    assert _is_dirty(f"?? a.sciproof.json{NUL}?? other.txt{NUL}") is True


def test_a_rename_is_checked_with_both_paths():
    assert _is_dirty(f"R  new.txt{NUL}old.txt{NUL}") is True
    assert _is_dirty(f"R  new.sciproof.json{NUL}old.sciproof.json{NUL}") is False


def test_a_detached_signature_of_a_manifest_is_not_a_code_change():
    assert _is_dirty(f"?? a.sciproof.json.sig{NUL}") is False
    assert _is_dirty(f"?? a.sciproof.json.sig{NUL}?? other.txt{NUL}") is True


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("git is not installed"),
        subprocess.TimeoutExpired(["git", "rev-parse", "HEAD"], 15),
        subprocess.CalledProcessError(128, ["git", "rev-parse", "HEAD"]),
    ],
    ids=["missing-git", "timeout", "nonzero-exit"],
)
def test_failed_commit_probe_returns_none_without_further_commands(tmp_path, monkeypatch, error):
    calls = []

    def fail_run(argv, **kwargs):
        calls.append((argv, kwargs["cwd"]))
        raise error

    monkeypatch.setattr(gitinfo.subprocess, "run", fail_run)
    assert git_info(tmp_path) is None
    assert calls == [(["git", "rev-parse", "HEAD"], tmp_path)]


@needs_git
def test_non_repository_returns_none(tmp_path):
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=tmp_path, capture_output=True
    )
    assert probe.returncode != 0, "fixture must be outside a repository"
    assert git_info(tmp_path) is None


@needs_git
def test_detached_head_preserves_commit_and_clean_state(repo):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    _git(repo, "checkout", "--detach", commit)
    assert git_info(repo) == {"commit": commit, "branch": "HEAD", "dirty": False}


@needs_git
@pytest.mark.parametrize("branch_fails", [False, True], ids=["empty", "failed"])
@pytest.mark.parametrize("dirty", [False, True], ids=["clean", "dirty"])
def test_missing_branch_preserves_commit_and_dirty_state(repo, monkeypatch, branch_fails, dirty):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    if dirty:
        (repo / "notes.txt").write_text("untracked")
    real_run = subprocess.run

    def branch_unavailable(argv, **kwargs):
        if argv == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            if branch_fails:
                raise subprocess.CalledProcessError(128, argv)
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(gitinfo.subprocess, "run", branch_unavailable)
    assert git_info(repo) == {"commit": commit, "branch": None, "dirty": dirty}
