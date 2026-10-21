"""Claims: mechanically checkable statements about values in output files."""

from __future__ import annotations

import json
import sys

import pytest

from sciproof import cli
from sciproof.claims import MISSING, claim_text, evaluate, json_safe, parse_claim
from sciproof.diffmanifests import diff_manifests
from sciproof.errors import CaptureError, ManifestError, UsageError
from sciproof.manifest import load_manifest
from sciproof.verify import Status, report_to_dict, verify

OUT = "out/result.json"


def _claim(expression, output=OUT):
    return parse_claim(output, expression)


def _holds(expression, value, output=OUT):
    claim = _claim(expression, output)
    location = claim["location"]
    return evaluate(claim, {location: value}).holds


# ---------------------------------------------------------------- parsing


def test_a_claim_is_parsed_into_its_parts():
    assert _claim("$.accuracy >= 0.9") == {
        "output": OUT,
        "location": "$.accuracy",
        "op": ">=",
        "value": 0.9,
    }
    assert _claim('$.label == "ok"')["value"] == "ok"
    assert _claim("$.a.b[2].c != null")["value"] is None
    assert _claim("$.flag == true")["value"] is True
    assert _claim('$.s == "two words"')["value"] == "two words"
    assert _claim("r3c1 < 10", "table.csv")["location"] == "r3c1"


def test_an_approximate_claim_carries_its_tolerances():
    claim = _claim("$.loss ~= 0.25 atol=0.01 rtol=0.001")
    assert (claim["op"], claim["value"], claim["rtol"], claim["atol"]) == ("~=", 0.25, 0.001, 0.01)
    exact = _claim("$.loss ~= 0.25")
    assert (exact["rtol"], exact["atol"]) == (0.0, 0.0)


@pytest.mark.parametrize(
    ("output", "expression", "message"),
    [
        (OUT, "$.a >=", "expected 'LOCATION OP VALUE'"),
        (OUT, "$.a => 1", "unknown comparison"),
        (OUT, "a >= 1", "not a location"),
        (OUT, "$.a b >= 1", "unknown comparison"),
        (OUT, "r1c1 >= 1", "not a location"),
        ("t.csv", "$.a >= 1", "not a location"),
        ("data.npy", "$.a >= 1", "support .json and .csv"),
        ("notes.txt", "$.a >= 1", "support .json and .csv"),
        (OUT, "$.a >= abc", "must be a JSON number"),
        (OUT, "$.a >= NaN", "must be a JSON number"),
        (OUT, "$.a >= Infinity", "must be a JSON number"),
        (OUT, "$.a >= [1]", "single number"),
        (OUT, '$.a < "x"', "needs a number"),
        (OUT, "$.a == 1 atol=0.1", "only apply to '~='"),
        (OUT, "$.a ~= 1 atol=-1", "not negative"),
        (OUT, "$.a ~= 1 atol=x", "must be a number"),
        (OUT, "$.a ~= 1 atol=1 atol=2", "given twice"),
        (OUT, "$.a#keys == 1", "not a location"),
    ],
)
def test_bad_claims_are_refused_with_an_explanation(output, expression, message):
    with pytest.raises(UsageError, match=message):
        parse_claim(output, expression)


def test_claim_text_is_what_a_person_would_write():
    assert claim_text(_claim("$.a >= 0.9")) == f"{OUT}: $.a >= 0.9"
    assert claim_text(_claim("$.a ~= 1 atol=0.5")) == f"{OUT}: $.a ~= 1 (rtol=0.0, atol=0.5)"
    assert claim_text(_claim('$.s == "x"')) == f'{OUT}: $.s == "x"'


# ---------------------------------------------------------------- evaluation


@pytest.mark.parametrize(
    ("expression", "found", "expected"),
    [
        ("$.v >= 0.9", 0.9, True),
        ("$.v >= 0.9", 0.8999, False),
        ("$.v > 0.9", 0.9, False),
        ("$.v <= 1", 1, True),
        ("$.v < 1", 1.0, False),
        ("$.v == 1", 1.0, True),  # an integer and a float with the same value
        ("$.v == 1", True, False),  # a boolean is not a number
        ("$.v == true", 1, False),
        ("$.v != 1", 2, True),
        ('$.v == "ok"', "ok", True),
        ('$.v == "ok"', "OK", False),
        ('$.v != "ok"', 3, True),
        ("$.v == null", None, True),
        ("$.v == 0", None, False),
        ("$.v ~= 0.25 atol=0.01", 0.2549, True),
        ("$.v ~= 0.25 atol=0.01", 0.27, False),
        ("$.v ~= 100 rtol=0.01", 100.99, True),
        ("$.v ~= 100 rtol=0.01", 102, False),
        ("$.v ~= 1", 1.0, True),  # no tolerance means exact
        ("$.v ~= 1", 1.0000001, False),
        ("$.v >= 0", float("nan"), False),
        ("$.v ~= 0 atol=1", float("nan"), False),
        ("$.v == 1", float("nan"), False),
        ("$.v != 1", float("nan"), True),
        ("$.v >= 1", float("inf"), True),
        ("$.v >= 1", "text", False),
        ("$.v < 5", True, False),  # a boolean is not a number for ordering either
    ],
)
def test_comparison_semantics(expression, found, expected):
    assert _holds(expression, found) is expected


def test_a_missing_location_does_not_hold_and_says_so():
    result = evaluate(_claim("$.nope >= 1"), {"$.v": 1})
    assert result.holds is False and result.found is MISSING
    assert "$.nope is not present" in result.message


def test_a_non_number_for_an_ordering_claim_is_explained():
    result = evaluate(_claim("$.v >= 1"), {"$.v": "text"})
    assert result.holds is False and "is not a number" in result.message
    assert "NaN" in evaluate(_claim("$.v >= 1"), {"$.v": float("nan")}).message


def test_json_safe_values_are_strict_json():
    assert json_safe(float("nan")) == "NaN"
    assert json_safe(float("-inf")) == "-Infinity"
    assert json_safe(1.5) == 1.5


# ---------------------------------------------------------------- capture


CLAIMS = [
    (OUT, "$.value >= 42"),
    (OUT, "$.value ~= 42 atol=0.5"),
    (OUT, '$.label == "ok"'),
]


def test_claims_are_recorded_in_the_manifest(project):
    result = project.capture(claims=CLAIMS)
    assert result.manifest["schema_version"] == "1.1"
    assert [claim_text(c) for c in result.manifest["claims"]] == [
        f"{OUT}: $.value >= 42",
        f"{OUT}: $.value ~= 42 (rtol=0.0, atol=0.5)",
        f'{OUT}: $.label == "ok"',
    ]
    assert not [w for w in result.warnings if "claim" in w]
    load_manifest(project.manifest)


def test_a_manifest_without_claims_has_none_and_stays_at_1_0(project):
    result = project.capture()
    assert "claims" not in result.manifest and result.manifest["schema_version"] == "1.0"


def test_a_claim_that_does_not_hold_when_captured_is_recorded_with_a_warning(project):
    result = project.capture(claims=[(OUT, "$.value >= 100")])
    (warning,) = [w for w in result.warnings if "claim" in w]
    assert "does not hold at capture time" in warning and "$.value is 42.0" in warning
    assert len(result.manifest["claims"]) == 1
    report = verify(project.manifest)
    assert report.status is Status.FAIL  # and verification keeps saying so


def test_a_claim_on_a_location_that_does_not_exist_warns(project):
    result = project.capture(claims=[(OUT, "$.missing >= 1")])
    assert any("is not present" in w for w in result.warnings)


def test_a_claim_needs_a_declared_output(project):
    with pytest.raises(CaptureError, match="must be declared with --output or --output-numeric"):
        project.capture(claims=[("data.json", "$.x >= 1")])
    with pytest.raises(CaptureError, match="must be declared"):
        project.capture(claims=[("elsewhere.json", "$.x >= 1")])


def test_a_claim_on_an_output_that_cannot_be_read_is_refused(project):
    with pytest.raises(UsageError, match="claims support .json and .csv"):
        project.capture(claims=[("out/exact.txt", "$.x >= 1")])
    code = "open('bad.json', 'w').write('{broken')"
    with pytest.raises(CaptureError, match="claim on 'bad.json'.*not valid JSON"):
        project.capture(
            argv=[sys.executable, "-c", code],
            inputs=["data.json"],
            outputs=["bad.json"],
            numeric_outputs=[],
            claims=[("bad.json", "$.x >= 1")],
        )


def test_claims_work_on_csv_outputs(project):
    code = "open('t.csv', 'w').write('epoch,loss\\n1,0.5\\n2,0.25\\n')"
    result = project.capture(
        argv=[sys.executable, "-c", code],
        inputs=["data.json"],
        outputs=["t.csv"],
        numeric_outputs=[],
        claims=[("t.csv", "r2c1 <= 0.25"), ("t.csv", 'r0c1 == "loss"')],
    )
    assert not [w for w in result.warnings if "claim" in w]
    assert verify(project.manifest).status is Status.PASS


# ---------------------------------------------------------------- verify


def test_claims_are_checked_and_pass(project):
    project.capture(claims=CLAIMS)
    report = verify(project.manifest)
    assert report.status is Status.PASS
    claim_checks = [c for c in report.checks if c.subject.startswith("claim ")]
    assert len(claim_checks) == 3 and all(c.status is Status.PASS for c in claim_checks)
    evidence = claim_checks[0].evidence
    assert evidence["kind"] == "claim" and evidence["phase"] == "recorded"
    assert evidence["found"] == 42.0 and evidence["present"] is True
    assert any("right criterion" in item for item in report.not_checked)


def test_no_claims_means_no_claim_note(project):
    project.capture()
    assert not any("right criterion" in item for item in verify(project.manifest).not_checked)


def test_a_claim_about_a_modified_output_is_not_judged(project):
    project.capture(claims=[(OUT, "$.value >= 1")])
    (project.root / "out" / "result.json").write_text('{"value": 1000, "label": "ok"}')
    report = verify(project.manifest)
    assert report.status is Status.FAIL  # the output itself no longer matches
    claim = next(c for c in report.checks if c.subject.startswith("claim "))
    assert claim.status is Status.INCONCLUSIVE
    assert "no longer matches the recorded evidence" in claim.message


def test_a_missing_output_does_not_let_a_claim_pass(project):
    project.capture(claims=[(OUT, "$.value >= 1")])
    (project.root / "out" / "result.json").unlink()
    claim = next(c for c in verify(project.manifest).checks if c.subject.startswith("claim "))
    assert claim.status is Status.INCONCLUSIVE


def test_a_claim_is_judged_again_on_the_rerun(project, monkeypatch):
    project.capture(rtol=1, claims=[(OUT, "$.value ~= 42 atol=0.001")])
    monkeypatch.setenv("SCIPROOF_TEST_EPS", "0.5")  # inside rtol=1, outside the claim
    report = verify(project.manifest, rerun=True, assume_yes=True)
    by_subject = {c.subject: c for c in report.checks}
    recorded = next(c for s, c in by_subject.items() if s.startswith("claim "))
    rerun = next(c for s, c in by_subject.items() if s.startswith("rerun claim "))
    assert recorded.status is Status.PASS and recorded.evidence["phase"] == "recorded"
    assert rerun.status is Status.FAIL and rerun.evidence["phase"] == "rerun"
    assert rerun.evidence["found"] == 42.5
    assert report.status is Status.FAIL


def test_claims_that_hold_on_the_rerun_pass_twice(project):
    project.capture(claims=CLAIMS)
    report = verify(project.manifest, rerun=True, assume_yes=True)
    assert report.status is Status.PASS
    assert len([c for c in report.checks if "claim" in c.subject]) == 6


def test_a_rerun_that_does_not_produce_the_file_is_inconclusive_for_the_claim(project):
    project.capture(claims=[(OUT, "$.value >= 1")])
    manifest = json.loads(project.manifest.read_text())
    manifest["command"]["argv"] = [sys.executable, "-c", "pass"]
    project.manifest.write_text(json.dumps(manifest))
    report = verify(project.manifest, rerun=True, assume_yes=True)
    rerun = next(c for c in report.checks if c.subject.startswith("rerun claim "))
    assert rerun.status is Status.INCONCLUSIVE and "did not produce" in rerun.message


def test_the_json_report_carries_the_claim_evidence_as_strict_json(project):
    project.capture(claims=CLAIMS)
    data = report_to_dict(verify(project.manifest))
    json.dumps(data, allow_nan=False)
    assert next(c for c in data["checks"] if c["subject"].startswith("claim "))["evidence"]["op"]


# ---------------------------------------------------------------- manifest validation


def _mutate(project, mutate):
    project.capture(claims=CLAIMS)
    data = json.loads(project.manifest.read_text())
    mutate(data)
    project.manifest.write_text(json.dumps(data))
    return project.manifest


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(schema_version="1.0"), "need schema_version 1.1"),
        (lambda d: d.update(claims="x"), "expected a list"),
        (lambda d: d.update(claims=["x"]), "expected an object"),
        (lambda d: d["claims"][0].update(output="data.json"), "must be a declared .json or .csv"),
        (
            lambda d: d["claims"][0].update(output="out/exact.txt"),
            "must be a declared .json or .csv",
        ),
        (lambda d: d["claims"][0].update(output="../x.json"), "'..'"),
        (lambda d: d["claims"][0].update(location="value"), "is not a location"),
        (lambda d: d["claims"][0].update(location="r1c1"), "is not a location"),
        (lambda d: d["claims"][0].update(op="=>"), "expected one of"),
        (lambda d: d["claims"][0].update(value="text"), "needs a finite number"),
        (lambda d: d["claims"][0].update(value=True), "needs a finite number"),
        (lambda d: d["claims"][0].update(value=[1]), "needs a finite number"),
        (lambda d: d["claims"][2].update(value=[1]), "expected a number, string"),
        (lambda d: d["claims"][1].update(rtol=-1), "finite number >= 0"),
        (lambda d: d["claims"][1].pop("atol"), "missing required|expected a number"),
        (lambda d: d["claims"][0].update(rtol=0.1), "only apply to '~='"),
        (lambda d: d.update(claims=d["claims"] * 40), "more than 100 claims"),
    ],
)
def test_invalid_claims_in_a_manifest_are_refused(project, mutate, message):
    path = _mutate(project, mutate)
    with pytest.raises(ManifestError, match=message):
        load_manifest(path)


def test_a_claim_on_an_absent_output_is_refused(project):
    def absent(data):
        out = next(o for o in data["outputs"] if o["path"] == OUT)
        out.update(present=False, sha256=None, size=0)

    with pytest.raises(ManifestError):
        load_manifest(_mutate(project, absent))


# ---------------------------------------------------------------- diff, inspect, command line


def test_diff_shows_added_and_removed_claims(project):
    first = project.capture(name="a", claims=[(OUT, "$.value >= 42")])
    second = project.capture(name="b", claims=[(OUT, "$.value >= 40"), (OUT, '$.label == "ok"')])
    changes = diff_manifests(first.manifest_path, second.manifest_path).changes
    kinds = {(c.kind, c.subject) for c in changes if c.section == "claims"}
    assert kinds == {
        ("removed", f"{OUT}: $.value >= 42"),
        ("added", f"{OUT}: $.value >= 40"),
        ("added", f'{OUT}: $.label == "ok"'),
    }


def test_cli_capture_inspect_and_verify(project, capsys):
    code = [
        "capture",
        "--name",
        "cli",
        "--input",
        "data.json",
        "--input",
        "experiment.py",
        "--output-numeric",
        OUT,
        "--claim",
        OUT,
        "$.value >= 42",
        "--claim",
        OUT,
        '$.label == "ok"',
        "--",
        sys.executable,
        "experiment.py",
    ]
    assert cli.main(code) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["inspect", "cli.sciproof.json"]) == cli.EXIT_OK
    shown = capsys.readouterr().out
    assert "Claims (2), checked by verify:" in shown and f"{OUT}: $.value >= 42" in shown
    assert cli.main(["verify", "cli.sciproof.json"]) == cli.EXIT_OK
    assert "holds: $.value is 42.0, claim is >= 42" in capsys.readouterr().out


def test_cli_rejects_a_bad_claim(project, capsys):
    bad = [
        "capture",
        "--name",
        "x",
        "--input",
        "data.json",
        "--output",
        OUT,
        "--claim",
        OUT,
        "nonsense",
    ]
    assert cli.main([*bad, "--", sys.executable, "experiment.py"]) == cli.EXIT_ERROR
    assert "expected 'LOCATION OP VALUE'" in capsys.readouterr().err


# ------------------------------------------------------- ints beyond float

HUGE = 10**400  # exact and finite, but no float can carry it


def test_an_approximate_claim_on_a_huge_int_compares_exactly():
    # ~= crashed with OverflowError on ints too large for a float (#88);
    # equal values satisfy the tolerance, anything else is a verdict.
    assert _holds("$.x ~= " + str(HUGE), HUGE) is True
    assert _holds("$.x ~= " + str(HUGE + 1), HUGE) is False
    assert _holds("$.x ~= 1", HUGE) is False


def test_a_huge_int_claim_value_is_a_valid_manifest_number(project):
    # == compares such ints exactly, so the manifest validator accepts the
    # value instead of crashing write_manifest (#88).
    path = _mutate(project, lambda d: d["claims"][0].update(value=HUGE))
    manifest = load_manifest(path)
    assert manifest["claims"][0]["value"] == HUGE


@pytest.mark.parametrize(
    ("mutate",),
    [
        (lambda d: d["execution"].update(duration_seconds=HUGE),),
        (lambda d: d["claims"][1].update(rtol=HUGE),),
    ],
)
def test_a_huge_int_in_a_validated_field_is_an_invalid_manifest(project, mutate):
    # A manifest is untrusted input: a number no float can carry is reported
    # as invalid instead of crashing every command that loads it (#88).
    path = _mutate(project, mutate)
    with pytest.raises(ManifestError, match="finite number >= 0"):
        load_manifest(path)
