"""Tests for manifest inspection: env vars, git, and zero outputs."""

from __future__ import annotations

from sciproof.inspect_manifest import describe
from sciproof.manifest import load_manifest


def test_inspect_shows_environment_variables(project):
    project.capture()
    manifest = load_manifest(project.manifest)
    manifest["environment"]["env_vars"] = {"FOO": "bar", "TZ": "UTC"}
    text = describe(manifest)
    assert "variables:" in text
    assert "FOO=bar" in text


def test_inspect_git_not_recorded(project):
    project.capture()
    manifest = load_manifest(project.manifest)
    manifest["git"] = None
    text = describe(manifest)
    assert "git:         not recorded" in text


def test_inspect_zero_outputs(project):
    project.capture()
    manifest = load_manifest(project.manifest)
    manifest["outputs"] = []
    text = describe(manifest)
    assert "Outputs (0):" in text
    assert "(none)" in text
