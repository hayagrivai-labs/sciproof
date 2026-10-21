# The verification report

`sciproof verify` explains its verdict. This page describes the text report and the versioned
JSON report (`--json`, schema `sciproof.report`, version `1.0`).

## What a report contains

* **Checks:** one line per fact that was checked, each `PASS`, `FAIL` or `INCONCLUSIVE`, with
  the subject (for example `input data.csv`) and a message.
* **Evidence (JSON):** the facts a check used, so a result can be audited without re-running
  anything: digests, tolerances, the numeric rule and the exact differences with their location.
* **Basis:** one sentence saying why the verdict was chosen (the aggregation rule below).
* **Not checked:** what this verification did *not* establish. This list is part of every report.
* **Warnings and notes:** things that do not change the verdict but matter for interpreting it,
  such as a dirty Git tree or a different environment.

## Verdicts and how they are combined

| Verdict | Meaning | Exit code |
| --- | --- | --- |
| `PASS` | Every check that was performed succeeded, and at least one check was performed. | `0` |
| `FAIL` | At least one check found something that contradicts the evidence. | `1` |
| `INCONCLUSIVE` | No check failed, but at least one could not be carried out, or none was performed. | `2` |

The aggregation is conservative: **one `FAIL` makes the whole result `FAIL`, even if other checks
are `INCONCLUSIVE`; any `INCONCLUSIVE` check prevents `PASS`.** Missing evidence is never read
as success. A report with no checks at all is `INCONCLUSIVE`.

## What a `PASS` does not say

Whatever the verdict, the report lists what was not established. Without `--rerun` this always
includes that the recorded command was **not executed**, so nothing is said about reproducibility.
It also always includes:

* scientific validity: a matching result is not shown to be correct;
* files that were not declared as inputs or outputs;
* authenticity: manifests are unsigned, so who produced the evidence is not verified (see
  [Signing manifests](DESIGN_SIGNING.md)).

After a rerun, the report adds that stdout/stderr are compared for information only, that the
environment is compared but not reconstructed, and that the result covers one re-execution on
this machine.

## JSON report (schema `sciproof.report` 1.0)

```json
{
  "schema": "sciproof.report",
  "schema_version": "1.0",
  "name": "demo",
  "result": "FAIL",
  "reason": "FAIL because 1 (output result.json) check(s) contradict the evidence; one failed check is enough",
  "counts": {"PASS": 2, "FAIL": 1, "INCONCLUSIVE": 0},
  "rerun_requested": false,
  "rerun_executed": false,
  "clean_env": false,
  "env_passed": [],
  "signature": null,
  "policy": null,
  "checks": [
    {
      "status": "FAIL",
      "subject": "output result.json",
      "message": "modified since capture (SHA-256 differs)",
      "evidence": {
        "kind": "output_integrity",
        "path": "result.json",
        "expected_sha256": "…",
        "actual_sha256": "…"
      }
    }
  ],
  "not_checked": ["reproducibility: the recorded command was not executed", "…"],
  "warnings": [],
  "notes": [],
  "rerun_outputs_dir": null
}
```

Compatibility: `name`, `result`, `rerun_requested`, `checks[].status|subject|message`,
`warnings`, `notes` and `rerun_outputs_dir` exist since 0.1.0 and keep their meaning. New fields
are added within `1.x`; a field is only removed or changed in a new major version of the schema.
`evidence` is `null` for checks that have no structured facts (for example an unsafe path).

### Evidence kinds

| `kind` | Used by | Fields |
| --- | --- | --- |
| `input_integrity`, `output_integrity`, `snapshot_integrity`, `rerun_output_exact` | digest comparisons | `path`, `expected_sha256`, `actual_sha256` (`null` when the file is absent) |
| `exit_code` | rerun | `recorded`, `rerun` |
| `policy` | `--policy` | `rule` (for example `manifest.max_age_days`), and where it applies `limit`, `found`, `path` |
| `claim` | a claim in the manifest | `phase` (`recorded` or `rerun`), `output`, `location`, `op`, `value`, `rtol`/`atol` for `~=`, `found` (`null` if the location is absent), `present` |
| `manifest_signature` | `--allowed-signers` | `namespace`, `signature_file`, `principal`, `key_type`, `fingerprint` (the last three are `null` unless the signature is good) |
| `rerun_output_numeric` | rerun of a `--output-numeric` file | `path`, `rtol`, `atol`, `rule`, `differences` (list of messages, empty when equivalent; `null` when the file could not be compared), `difference_details`, `error` |

`signature` is `null` unless `--allowed-signers` was given; then it holds `status` (`good`, `bad`,
`missing` or `unavailable`) and the same fields as the signature evidence ([Signing](SIGNING.md)).

`policy` is `null` unless `--policy` was given; then it holds the `file` name, its `sha256` and the
policy `version`, and every rule is a check whose subject starts with `policy` ([Policy](POLICY.md)).

`clean_env` is true when the rerun used `--clean-env`; `env_passed` then lists the *names*
(never the values) of the environment variables the command received.

Each entry of `difference_details` has `kind` (`number`, `value`, `structure` or `summary`),
`location`, `recorded`, `found`, and for two numbers `abs_diff`, `rel_diff` (relative to the
larger magnitude) and `allowed` (the largest absolute difference the tolerance would have
accepted); they are `null` where undefined, and NaN or infinite values appear as the strings
`"NaN"`, `"Infinity"`, `"-Infinity"` so that the document is strict JSON.

## Markdown

`sciproof verify --format markdown` (or `Report.to_markdown()`) prints the same report as
GitHub-flavoured Markdown: the verdict and reason, a table of checks, a table of numeric
differences, warnings, and what was not checked. Text that comes from a manifest is escaped.
In GitHub Actions: `sciproof verify baseline.sciproof.json --format markdown >> "$GITHUB_STEP_SUMMARY"`.

For a directory, the digest fields hold the tree digests and the evidence adds
`artifact: "directory"`, `files_expected`, `files_actual` and `tree`, which has `added`,
`removed` and `modified` (at most 10 names each) with `added_count`, `removed_count` and
`modified_count`; `tree` is `null` when nothing differs. A directory that cannot be read
as evidence (a symbolic link inside) has a `problem` string instead.

The report is a statement about the checks listed in it. It does not claim that the experiment
as a whole is reproducible, and it is not signed.
