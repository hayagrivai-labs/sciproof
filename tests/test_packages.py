"""The fast package listing must give the same answer as reading every METADATA file."""

from __future__ import annotations

import importlib.metadata as md
import sys
from pathlib import PurePosixPath

import pytest

from sciproof.diffmanifests import diff_manifests
from sciproof.environment import (
    _LIST_PACKAGES_SOURCE,
    interpreter_info,
    list_packages,
    normalize_package_name,
    platform_info,
    selected_env_vars,
)
from sciproof.verify import _compare_environment


def _exact():
    packages = {}
    for dist in md.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if name:
            packages[normalize_package_name(name)] = dist.version
    return packages


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("NumPy", "numpy"),
        ("typing_extensions", "typing-extensions"),
        ("jaraco.context", "jaraco-context"),
        ("Jaraco__Context.X", "jaraco-context-x"),
        ("a-b_c.d", "a-b-c-d"),
        ("PyYAML", "pyyaml"),
    ],
)
def test_package_names_are_normalised_the_standard_way(name, expected):
    assert normalize_package_name(name) == expected


def test_the_fast_listing_matches_the_exact_one_on_this_environment():
    fast = {normalize_package_name(n): v for n, v in list_packages()}
    assert fast == _exact()
    assert "pytest" in fast  # something that is certainly installed


def test_the_listing_is_sorted_and_has_names_and_versions():
    packages = list_packages()
    assert packages == sorted(packages, key=lambda item: item[0].lower())
    assert all(len(item) == 2 and item[0] and item[1] for item in packages)


def test_the_child_interpreter_lists_the_same_packages(tmp_path):
    """The experiment's interpreter runs the same source text, without SciProof."""
    info, source = interpreter_info(sys.executable, tmp_path)
    assert source == "command-interpreter"
    assert info["packages"] == list_packages()


class _Dist:
    def __init__(self, path=None, name=None, version="0"):
        if path is not None:
            self._path = PurePosixPath(path)
        self.metadata = {"Name": name}
        self.version = version


def test_the_slow_exact_way_is_used_when_the_directory_name_does_not_say(monkeypatch):
    dists = [
        _Dist("foo_bar-1.2.3.dist-info", "Foo-Bar", "9.9.9"),  # the directory wins, as fast
        _Dist("my-pkg-3d-2.0.dist-info", "ignored", "x"),  # a hyphenated name
        _Dist("old-1.0-py3.11.egg-info", "Old.Pkg", "1.0"),  # an egg-info with a python tag
        _Dist(None, "No.Path", "5"),  # a distribution that has no directory
        _Dist("EGG-INFO", "Zipped_Egg", "7"),  # a zipped egg
        _Dist("dir-with-no-name.dist-info", "", "1"),  # nothing usable
    ]
    monkeypatch.setattr(md, "distributions", lambda: dists)
    found = {normalize_package_name(n): (n, v) for n, v in list_packages()}
    assert found["foo-bar"] == ("foo_bar", "1.2.3")
    assert found["my-pkg-3d"] == ("my-pkg-3d", "2.0")
    assert found["old-pkg"] == ("Old.Pkg", "1.0")
    assert found["no-path"] == ("No.Path", "5")
    assert found["zipped-egg"] == ("Zipped_Egg", "7")
    assert "dir-with-no-name" not in found and "" not in found


def test_the_source_text_is_self_contained():
    namespace: dict = {}
    exec(_LIST_PACKAGES_SOURCE, namespace)  # noqa: S102
    assert callable(namespace["list_packages"])
    assert "import re" in _LIST_PACKAGES_SOURCE  # nothing from SciProof is needed


# ---------------------------------------------------------------- old manifests keep working


def _manifest_with(dependencies):
    return {
        "command": {"argv": [sys.executable]},
        "environment": {
            "python": {"version": "3.0.0"},
            "platform": platform_info(),
            "env_vars": selected_env_vars(),
        },
        "dependencies": dependencies,
    }


def test_a_manifest_recorded_with_dotted_names_still_matches(monkeypatch, tmp_path):
    """0.2.x recorded 'jaraco.context' (from METADATA); now it is found as 'jaraco_context'."""
    monkeypatch.setattr(
        "sciproof.verify.interpreter_info",
        lambda argv0, cwd: ({"version": "3.0.0", "packages": [["jaraco_context", "6.1.2"]]}, "x"),
    )
    manifest = _manifest_with([{"name": "jaraco.context", "version": "6.1.2"}])
    assert _compare_environment(tmp_path, manifest) == []
    manifest = _manifest_with([{"name": "jaraco.context", "version": "6.0.0"}])
    assert _compare_environment(tmp_path, manifest) == [
        "package jaraco.context: 6.0.0 was used, now 6.1.2"
    ]


def test_diff_treats_the_two_spellings_as_one_package(project):
    import json

    first = project.capture(name="a")
    data = json.loads(first.manifest_path.read_text())
    data["dependencies"] = [{"name": "jaraco.context", "version": "1"}]
    (project.root / "b.sciproof.json").write_text(json.dumps(data))
    data["dependencies"] = [{"name": "jaraco_context", "version": "1"}]
    (project.root / "c.sciproof.json").write_text(json.dumps(data))
    report = diff_manifests(project.root / "b.sciproof.json", project.root / "c.sciproof.json")
    assert not [c for c in report.changes if c.subject.startswith("package")]
