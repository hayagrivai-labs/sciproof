"""Multi-step pipeline DAG tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sciproof.capture import capture
from sciproof.cli import main
from sciproof.errors import UsageError
from sciproof.pipeline import load_pipeline, verify_pipeline
from sciproof.verify import Status


def test_pipeline_dag_success_and_chain_of_custody(tmp_path: Path):
    data_file = tmp_path / "data.csv"
    data_file.write_text("x,y\n1,2\n3,4\n", encoding="utf-8")

    # Step 1: data prep produces features.csv
    features_file = tmp_path / "features.csv"
    features_file.write_text("f1,f2\n1.0,2.0\n", encoding="utf-8")
    prep_res = capture(
        name="step1_prep",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        inputs=["data.csv"],
        outputs=["features.csv"],
    )

    # Step 2: train takes features.csv and produces metrics.json
    metrics_file = tmp_path / "metrics.json"
    metrics_file.write_text(json.dumps({"accuracy": 0.98}), encoding="utf-8")
    train_res = capture(
        name="step2_train",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        inputs=["features.csv"],
        outputs=["metrics.json"],
    )

    pipeline_toml = tmp_path / "pipeline.toml"
    pipeline_toml.write_text(
        f"""[pipeline]
name = "ml_workflow"

[[steps]]
name = "prep"
manifest = "{prep_res.manifest_path.name}"

[[steps]]
name = "train"
manifest = "{train_res.manifest_path.name}"
depends_on = ["prep"]
""",
        encoding="utf-8",
    )

    report = verify_pipeline(pipeline_toml)
    assert report.status is Status.PASS
    assert len(report.steps) == 2
    assert "Overall Verdict: PASS" in report.to_text()


def test_pipeline_chain_of_custody_failure(tmp_path: Path):
    # Step 1 output
    features_file = tmp_path / "features.csv"
    features_file.write_text("original", encoding="utf-8")
    prep_res = capture(
        name="step1",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["features.csv"],
    )

    # Tamper with features before Step 2
    features_file.write_text("tampered", encoding="utf-8")
    train_res = capture(
        name="step2",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        inputs=["features.csv"],
        outputs=[],
    )

    # Restore file so step integrity check alone might pass for step 2,
    # but the recorded input sha for step 2 differs from step 1's output
    pipeline_toml = tmp_path / "pipeline.toml"
    pipeline_toml.write_text(
        f"""[pipeline]
name = "broken_workflow"

[[steps]]
name = "s1"
manifest = "{prep_res.manifest_path.name}"

[[steps]]
name = "s2"
manifest = "{train_res.manifest_path.name}"
depends_on = ["s1"]
""",
        encoding="utf-8",
    )

    report = verify_pipeline(pipeline_toml)
    assert report.status is Status.FAIL
    assert any("chain-of-custody break" in report.to_text() for s in report.steps)


def test_pipeline_cli_verify(tmp_path: Path, capsys):
    out_file = tmp_path / "res.txt"
    out_file.write_text("done", encoding="utf-8")
    res = capture(
        name="step_cli",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["res.txt"],
    )
    pipeline_toml = tmp_path / "pipeline.toml"
    pipeline_toml.write_text(
        f"""[pipeline]
name = "cli_pipe"

[[steps]]
name = "step1"
manifest = "{res.manifest_path.name}"
""",
        encoding="utf-8",
    )

    code = main(["pipeline", "verify", str(pipeline_toml)])
    assert code == 0
    captured = capsys.readouterr()
    assert "SciProof Pipeline: cli_pipe" in captured.out
    assert "Overall Verdict: PASS" in captured.out


def test_pipeline_missing_file_raises(tmp_path: Path):
    with pytest.raises(UsageError, match="pipeline file not found"):
        load_pipeline(tmp_path / "missing.toml")


def _write_quiet_pipeline(tmp_path: Path, *, tampered: bool) -> Path:
    features = tmp_path / "features.csv"
    features.write_text("original", encoding="utf-8")
    prep = capture(
        name="quiet_prep",
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=["features.csv"],
    )
    depends = ""
    if tampered:
        features.write_text("tampered", encoding="utf-8")
        train = capture(
            name="quiet_train",
            argv=["python", "-c", "pass"],
            base=tmp_path,
            inputs=["features.csv"],
            outputs=[],
        )
        depends = f"""
[[steps]]
name = "train"
manifest = "{train.manifest_path.name}"
depends_on = ["prep"]
"""
    toml = tmp_path / "pipeline.toml"
    toml.write_text(
        f"""[pipeline]
name = "quiet_pipe"

[[steps]]
name = "prep"
manifest = "{prep.manifest_path.name}"
{depends}""",
        encoding="utf-8",
    )
    return toml


def test_pipeline_quiet_pass(tmp_path: Path, capsys):
    toml = _write_quiet_pipeline(tmp_path, tampered=False)
    assert main(["pipeline", "verify", str(toml), "-q"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "RESULT: PASS"
    assert captured.err == ""


def test_pipeline_quiet_fail_reports_details_on_stderr(tmp_path: Path, capsys):
    toml = _write_quiet_pipeline(tmp_path, tampered=True)
    assert main(["pipeline", "verify", str(toml), "-q"]) == 1
    captured = capsys.readouterr()
    assert captured.out.strip() == "RESULT: FAIL"
    assert "chain-of-custody break" in captured.err


def test_pipeline_chain_of_custody_violation_overrides_inconclusive(tmp_path: Path):
    prep_dir = tmp_path / "prep"
    train_dir = tmp_path / "train"
    prep_dir.mkdir()
    train_dir.mkdir()

    (prep_dir / "mid.txt").write_text("v2", encoding="utf-8")
    prep = capture(
        name="step1",
        argv=["python", "-c", "pass"],
        base=prep_dir,
        outputs=["mid.txt"],
    )

    (train_dir / "mid.txt").write_text("v1", encoding="utf-8")
    (train_dir / "extra.txt").write_text("cfg", encoding="utf-8")
    (train_dir / "final.txt").write_text("v1!", encoding="utf-8")
    train = capture(
        name="step2",
        argv=["python", "-c", "pass"],
        base=train_dir,
        inputs=["mid.txt", "extra.txt"],
        outputs=["final.txt"],
    )
    (train_dir / "extra.txt").unlink()

    pipeline_toml = tmp_path / "pipeline.toml"
    pipeline_toml.write_text(
        f"""[pipeline]
name = "mixed_verdict"

[[steps]]
name = "step1"
manifest = "prep/{prep.manifest_path.name}"

[[steps]]
name = "step2"
manifest = "train/{train.manifest_path.name}"
depends_on = ["step1"]
""",
        encoding="utf-8",
    )

    report = verify_pipeline(pipeline_toml)
    step2 = report.steps[1]
    assert step2.status is Status.FAIL
    assert "chain-of-custody broken" in step2.reason
    assert "INCONCLUSIVE because" in step2.reason
    assert report.status is Status.FAIL


def _capture_step(tmp_path: Path, name: str) -> str:
    """Capture a trivial step that writes <name>.out; return its manifest filename."""
    out = tmp_path / f"{name}.out"
    out.write_text(name, encoding="utf-8")
    res = capture(
        name=name,
        argv=["python", "-c", "pass"],
        base=tmp_path,
        outputs=[out.name],
    )
    return res.manifest_path.name


def test_pipeline_self_dependency_raises(tmp_path: Path):
    manifest = _capture_step(tmp_path, "train")
    pipeline_toml = tmp_path / "pipeline.toml"
    pipeline_toml.write_text(
        f"""[pipeline]
name = "self_dep"

[[steps]]
name = "train"
manifest = "{manifest}"
depends_on = ["train"]
""",
        encoding="utf-8",
    )

    with pytest.raises(UsageError, match="step 'train' cannot depend on itself"):
        verify_pipeline(pipeline_toml)


def test_pipeline_forward_reference_raises(tmp_path: Path):
    train_manifest = _capture_step(tmp_path, "train")
    eval_manifest = _capture_step(tmp_path, "eval")
    # eval is listed BEFORE train, but depends on it.
    pipeline_toml = tmp_path / "pipeline.toml"
    pipeline_toml.write_text(
        f"""[pipeline]
name = "forward_ref"

[[steps]]
name = "eval"
manifest = "{eval_manifest}"
depends_on = ["train"]

[[steps]]
name = "train"
manifest = "{train_manifest}"
""",
        encoding="utf-8",
    )

    with pytest.raises(UsageError, match="step 'eval' depends on 'train', which has not run yet"):
        verify_pipeline(pipeline_toml)


def test_pipeline_two_step_cycle_raises(tmp_path: Path):
    a_manifest = _capture_step(tmp_path, "a")
    b_manifest = _capture_step(tmp_path, "b")
    pipeline_toml = tmp_path / "pipeline.toml"
    pipeline_toml.write_text(
        f"""[pipeline]
name = "cycle"

[[steps]]
name = "a"
manifest = "{a_manifest}"
depends_on = ["b"]

[[steps]]
name = "b"
manifest = "{b_manifest}"
depends_on = ["a"]
""",
        encoding="utf-8",
    )

    with pytest.raises(UsageError, match="which has not run yet"):
        verify_pipeline(pipeline_toml)
