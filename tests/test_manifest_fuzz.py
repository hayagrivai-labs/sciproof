"""Every field of a valid manifest, damaged in turn: no command may end in a traceback.

A manifest is untrusted input. Whatever its content, the commands that read it must either do their
job or raise a ``SciProofError`` (reported as ``error: ...`` with exit status 3); any other
exception is a bug. This test found several such bugs by hand (#88, #89, #90); it keeps the whole
class closed: each field is deleted and replaced with values of the wrong type, the wrong range
and the wrong size, and the readers are run on the result.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import pytest

from sciproof import export_in_toto, export_openlineage, load_manifest, verify_manifest
from sciproof.diffmanifests import diff_manifests
from sciproof.errors import SciProofError
from sciproof.inspect_manifest import describe
from sciproof.junit import report_junit
from sciproof.ledger import list_manifests
from sciproof.markdown import report_markdown
from sciproof.verify import report_to_dict

HUGE = 10**400  # an int that no float can carry
MISSING = object()
VALUES = [
    None,
    "x",
    "",
    -1,
    0,
    1.5,
    -0.0,
    True,
    [],
    {},
    HUGE,
    2**63,
    1e300,
    "../a",
    "0001-01-01T00:00:00+00:00",
    "9999-12-31T23:59:59+00:00",
    MISSING,
]


def _locations(node: Any, path: tuple = ()) -> Iterator[tuple]:
    if path:
        yield path
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _locations(value, (*path, key))
    elif isinstance(node, list):
        # two items show how the list is read; the hundredth package is read like the first
        for index, value in enumerate(node[:2]):
            yield from _locations(value, (*path, index))


def _damaged(base: dict[str, Any]) -> Iterator[tuple[tuple, Any, dict[str, Any]]]:
    for path in _locations(base):
        for value in VALUES:
            data = copy.deepcopy(base)
            parent = data
            for key in path[:-1]:
                parent = parent[key]
            if value is MISSING:
                del parent[path[-1]]
            else:
                parent[path[-1]] = value
            yield path, value, data


def _use_everywhere(path, baseline):
    """Run every reader on the manifest at ``path``; ``SciProofError`` is a clean refusal."""
    try:
        manifest = load_manifest(path)
    except SciProofError:
        return
    describe(manifest)
    report = verify_manifest(path)
    report_to_dict(report)
    report_markdown(report)
    report_junit(report)
    export_in_toto(path)
    export_openlineage(path)
    diff_manifests(baseline, path).to_markdown()
    list_manifests(path.parent, check=True)


@pytest.fixture
def rich_project(project):
    """A manifest that uses every optional section: claims, a note, hardware, a directory."""
    (project.root / "dataset").mkdir()
    (project.root / "dataset" / "a.txt").write_text("a")
    project.capture(
        inputs=["data.json", "experiment.py", "dataset"],
        claims=[
            ("out/result.json", "$.value >= 42"),
            ("out/result.json", "$.value ~= 42 rtol=0.01 atol=0.5"),
        ],
        note="baseline",
        record_hardware=True,
    )
    return project


def test_no_damaged_manifest_ends_in_a_traceback(rich_project, tmp_path):
    baseline = rich_project.manifest
    base = json.loads(baseline.read_text())
    work = rich_project.root / "damaged"
    work.mkdir()
    target = work / "exp.sciproof.json"
    failures = []
    for path, value, data in _damaged(base):
        target.write_text(json.dumps(data))
        try:
            _use_everywhere(target, baseline)
        except SciProofError:
            continue
        except Exception as exc:  # the point of the test: nothing else may escape
            shown = "(deleted)" if value is MISSING else "HUGE" if value is HUGE else repr(value)
            failures.append(f"{'.'.join(map(str, path))} = {shown}: {type(exc).__name__}: {exc}")
    assert not failures, "unhandled exceptions:\n" + "\n".join(failures[:20])
