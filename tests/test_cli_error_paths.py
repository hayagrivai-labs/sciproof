from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    source = str(Path(__file__).parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": source}
    return subprocess.run(
        [sys.executable, "-m", "sciproof", *args],
        cwd=root,
        capture_output=True,
        env=env,
        text=True,
        check=False,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture
def cli_error_project(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "work"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"unchanged")
    (root / "input.txt").write_text("input")

    created = _run(
        root,
        [
            "capture",
            "--name",
            "valid",
            "--input",
            "input.txt",
            "--",
            sys.executable,
            "-c",
            "pass",
        ],
    )
    assert created.returncode == 0, created.stderr
    (root / "empty.json").write_text("")
    (root / "wrong-shape.json").write_text("[]")
    (root / "huge.json").write_text("9" * 5000)
    (root / "directory").mkdir()
    return root, outside


@pytest.mark.parametrize(
    ("case", "args", "expected"),
    [
        (
            "capture missing input",
            [
                "capture",
                "--name",
                "missing",
                "--input",
                "missing.txt",
                "--",
                sys.executable,
                "-c",
                "pass",
            ],
            3,
        ),
        (
            "capture bad option value",
            [
                "capture",
                "--name",
                "bad-value",
                "--rtol",
                "not-a-number",
                "--input",
                "input.txt",
                "--",
                sys.executable,
                "-c",
                "pass",
            ],
            3,
        ),
        ("verify missing manifest", ["verify", "missing.json"], 3),
        ("verify directory", ["verify", "directory"], 3),
        ("verify empty manifest", ["verify", "empty.json"], 3),
        ("verify wrong-shaped manifest", ["verify", "wrong-shape.json"], 3),
        ("verify huge number", ["verify", "huge.json"], 3),
        (
            "verify output directory",
            ["verify", "valid.sciproof.json", "--save-json", "directory"],
            3,
        ),
        ("diff missing manifest", ["diff", "missing.json", "valid.sciproof.json"], 3),
        ("diff directory", ["diff", "directory", "valid.sciproof.json"], 3),
        ("diff empty manifest", ["diff", "empty.json", "valid.sciproof.json"], 3),
        (
            "diff wrong-shaped manifest",
            ["diff", "wrong-shape.json", "valid.sciproof.json"],
            3,
        ),
        ("diff huge number", ["diff", "huge.json", "valid.sciproof.json"], 3),
        (
            "diff bad option value",
            ["diff", "valid.sciproof.json", "valid.sciproof.json", "--format", "invalid"],
            3,
        ),
        ("export missing manifest", ["export", "missing.json"], 3),
        ("export directory", ["export", "directory"], 3),
        ("export empty manifest", ["export", "empty.json"], 3),
        ("export wrong-shaped manifest", ["export", "wrong-shape.json"], 3),
        ("export huge number", ["export", "huge.json"], 3),
        (
            "export bad option value",
            ["export", "valid.sciproof.json", "--format", "invalid"],
            3,
        ),
        (
            "export output directory",
            ["export", "valid.sciproof.json", "-o", "directory"],
            3,
        ),
        (
            "export missing output parent",
            ["export", "valid.sciproof.json", "-o", "missing/out.json"],
            3,
        ),
    ],
)
def test_cli_error_paths_are_clean(
    cli_error_project: tuple[Path, Path], case: str, args: list[str], expected: int
) -> None:
    root, outside = cli_error_project
    before = _snapshot(outside)
    completed = _run(root, args)

    assert completed.returncode == expected, f"{case}: {completed.stderr}"
    assert "Traceback" not in completed.stderr, case
    assert _snapshot(outside) == before, case


def test_cli_duplicate_options_are_a_documented_result(
    cli_error_project: tuple[Path, Path],
) -> None:
    root, outside = cli_error_project
    before = _snapshot(outside)
    completed = _run(
        root,
        ["verify", "valid.sciproof.json", "--format", "text", "--format", "json"],
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["result"] == "PASS"
    assert "Traceback" not in completed.stderr
    assert _snapshot(outside) == before


def test_cli_unreadable_manifest_is_clean_when_permissions_are_supported(
    cli_error_project: tuple[Path, Path],
) -> None:
    root, outside = cli_error_project
    unreadable = root / "unreadable.json"
    unreadable.write_text("{}")
    unreadable.chmod(0)
    try:
        completed = _run(root, ["verify", "unreadable.json"])
        if completed.returncode == 0:
            pytest.skip("the test process can read chmod 0 files")
        assert completed.returncode == 3
        assert "Traceback" not in completed.stderr
        assert _snapshot(outside) == {"sentinel": b"unchanged"}
    finally:
        unreadable.chmod(0o600)
