# Python API

The command line is the primary interface. The same functionality is available from Python, for
tests, notebooks and CI scripts. The names listed on this page are the supported API; everything
else in the package is internal and may change in any release.

```python
from pathlib import Path

import sciproof

result = sciproof.capture_experiment(
    name="baseline",
    argv=["python", "train.py"],
    base=Path("."),
    inputs=["params.json", "train.py"],
    numeric_outputs=["metrics.json"],
    rtol=1e-6,
)

report = sciproof.verify_manifest(result.manifest_path)
if report.status is not sciproof.Status.PASS:
    print(report.reason)
```

A complete, runnable version is `examples/api_usage.py`.

## Functions

### `capture_experiment(*, name, argv, base, inputs=None, outputs=None, numeric_outputs=None, rtol=1e-9, atol=0.0, timeout=None, max_log_bytes=..., force=False, claims=None, note=None, record_hardware=False) -> CaptureResult`

Runs `argv` once in `base` (no shell) and writes `<name>.sciproof.json` there. The arguments
mean what the `sciproof capture` options mean; paths are relative to `base`. A command that exits
nonzero still produces evidence; check `result.exit_code`. `result.warnings` holds what the CLI
prints as `warning:` lines (for example `rtol >= 1`). Raises `CaptureError` if the evidence cannot
be written.

### `verify_manifest(manifest_path, *, rerun=False, assume_yes=False, strict_env=False, timeout=3600.0, confirm=None, clean_env=False, pass_env=(), allowed_signers=None, policy=None) -> Report`

Verifies a manifest. Without `rerun` nothing is executed and only integrity is checked.

With `rerun=True` the recorded command is executed, which is **arbitrary code execution** with
your permissions. It needs a confirmation: `assume_yes=True`, or a `confirm(prompt) -> bool`
callback that receives the exact command and context. With neither, `ConfirmationRequired` is
raised and nothing runs. `clean_env=True` gives the command a minimal environment instead of all
of yours (credentials included); `pass_env=["NAME", ...]` adds variables to it. Both are only
meaningful with `rerun`.

Raises `ManifestError` for an unreadable, invalid or unsafe manifest and `UsageError` for invalid
arguments. A verdict of `FAIL` or `INCONCLUSIVE` is **not** an exception: it is `report.status`.

`policy` is the path of a [policy file](POLICY.md); an invalid one raises `PolicyError`
(a `SciProofError`) before anything is executed. `Report.to_markdown()` and
`sciproof.junit.report_junit(report)` print the report as Markdown and JUnit XML.

### `update_baseline(manifest_path, *, assume_yes=False, confirm=None, note=None, timeout=None) -> UpdateResult | None`

Captures the experiment of a manifest again with its own declarations and returns an
`UpdateResult` with `capture` (the new `CaptureResult`), `diff` (a `DiffReport` of old against
new) and `warnings`; `None` if the confirmation was declined. It **executes the recorded
command**: without `assume_yes` or a `confirm` callback it raises `ConfirmationRequired`
([Updating a baseline](UPDATE.md)).

### `diff_manifests(old_path, new_path) -> DiffReport`

Compares two manifests and returns what changed between the two recorded runs (see
[Comparing two runs](DIFF.md)). `report.identical`, `report.summary`, `report.changes`,
`report.to_dict()`, `report.to_text()` and `report.to_markdown()`. Executes nothing.

### `sign_manifest(manifest_path, key_path) -> Path`

Signs a manifest with an OpenSSH private key and returns the path of the `.sig` file; raises
`SignatureError`. `verify_manifest(..., allowed_signers=path)` adds the signature check to the
report, and `report.signature` holds its outcome ([Signing](SIGNING.md)).

### `list_manifests(directory, *, recursive=False, check=False) -> list[Entry]`

The manifests in a directory, newest first, optionally verified (integrity only);
`export_in_toto(manifest_path) -> dict` returns the manifest as an in-toto Statement and
`export_openlineage(manifest_path) -> list[dict]` as OpenLineage run events
([Listing and exporting](LIST_AND_EXPORT.md)).

### `load_manifest(path) -> dict`

Reads and strictly validates a manifest (repeated keys, unsafe paths and bad hashes raise
`ManifestError`). Use it to read fields; it executes nothing.

### `report_to_dict(report) -> dict` and `Report.to_dict()`

The report as plain data in the `sciproof.report` schema, identical to `sciproof verify --json`
([Report](REPORT.md)). `Report.to_markdown()` returns it as GitHub-flavoured Markdown.

## Types

* `Status`: `PASS`, `FAIL`, `INCONCLUSIVE` (a `StrEnum`; `status.value` is the string).
* `Report`: `status`, `reason`, `not_checked`, `checks`, `warnings`, `info`, `rerun_executed`,
  `clean_env`, `env_passed`, `rerun_outputs_dir`, `to_dict()`.
* `Check`: `status`, `subject`, `message`, `evidence` (a dict, or `None`).
* `CaptureResult`: `manifest_path`, `manifest`, `warnings`, `exit_code`.
* Exceptions, all subclasses of `SciProofError`: `ManifestError`, `CaptureError`,
  `UsageError`, `ConfirmationRequired`.

## Using it in a test

```python
def test_metrics_have_not_drifted():
    report = sciproof.verify_manifest(
        "baseline.sciproof.json", rerun=True, assume_yes=True, clean_env=True
    )
    assert report.status is sciproof.Status.PASS, report.reason
```

## Stability

SciProof is alpha software (0.x). The names above are intended to stay; a change to them is
listed in the changelog and, from 1.0, follows semantic versioning. The package ships a
`py.typed` marker, so type checkers use the annotations. The module names `sciproof.verify` and
`sciproof.capture` are still modules; the functions are exported as `verify_manifest` and
`capture_experiment` to avoid shadowing them.
