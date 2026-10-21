# Using SciProof in CI

A manifest committed next to your results lets a CI job re-check them. The job below verifies
**integrity only**: nothing recorded in the manifest is executed.

```yaml
name: evidence
on: [pull_request]

permissions:
  contents: read

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install SciProof
        run: python -m pip install "git+https://github.com/hayagrivai-labs/sciproof.git"
      - name: Verify the evidence
        run: sciproof verify baseline.sciproof.json --json | tee verify.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: verify-report
          path: verify.json
```

The job fails when the verdict is `FAIL` (exit code `1`), `INCONCLUSIVE` (`2`) or the manifest
is invalid (`3`); see [Exit codes](EXIT_CODES.md). With `--json` the report on stdout is a single
JSON object with `name`, `result`, `checks`, `warnings` and `notes`.

> Pin the actions you use to a commit SHA if your organisation requires it. The versions above
> only illustrate the shape of the workflow.

## Re-running in CI

`sciproof verify --rerun --yes` **executes the command recorded in the manifest** with the
permissions of the job. Only do this for manifests you trust, for example ones committed by your
own team to a protected branch.

* Do not use `pull_request_target` or any trigger that gives untrusted code a privileged token.
  A manifest from a pull request contains a command chosen by its author.
* Keep `permissions` minimal (`contents: read`) and expose no secrets to the job.
* Add `--strict-env` if a rerun on a different Python version or platform should be reported as
  `INCONCLUSIVE` instead of being attempted.
* Add `--clean-env` so the recorded command receives only a minimal environment. Without it the
  command sees every variable of the job, including any token you exposed. Pass what it
  really needs with `--pass-env NAME`. This does not restrict files or network access.

```yaml
      - name: Re-execute the recorded command (trusted manifests only)
        run: sciproof verify baseline.sciproof.json --rerun --yes --strict-env --clean-env --json
```

## Showing the result in the job summary

`--format markdown` writes the report as Markdown, with a table of the numeric differences, which
GitHub shows on the run page when it is appended to `$GITHUB_STEP_SUMMARY`. The exit code is
unchanged, so the step still fails on `FAIL` or `INCONCLUSIVE` and the summary is written either way.

```yaml
      - name: Verify the recorded experiment
        run: sciproof verify baseline.sciproof.json --format markdown >> "$GITHUB_STEP_SUMMARY"
```

To see what changed between the committed baseline and a fresh capture, use
`sciproof diff baseline.sciproof.json candidate.sciproof.json --format markdown`.

## The GitHub Action

The repository is also a composite action that does the above for you: it installs SciProof (or uses
the one you installed), verifies a manifest, writes the Markdown report to the job summary, and fails
the step unless the result is `PASS`.

```yaml
      - uses: actions/checkout@v7
      - uses: hayagrivai-labs/sciproof@v0.3.0
        with:
          manifest: baseline.sciproof.json
          policy: sciproof.policy.toml     # optional
          allowed-signers: allowed_signers # optional: also check the .sig signature
```

| Input | Default | Meaning |
| --- | --- | --- |
| `manifest` | (required) | the manifest to verify |
| `rerun` | `false` | re-execute the recorded command (**runs the manifest's command**, see above) |
| `clean-env` | `true` | with `rerun`: give the command a minimal environment |
| `policy` | | a [policy file](POLICY.md) |
| `allowed-signers` | | an OpenSSH allowed-signers file ([signing](SIGNING.md)) |
| `working-directory` | `.` | the directory the paths are relative to |
| `version` | latest | the SciProof version to install, for example `0.3.0` (the action needs 0.3.0 or later and fails with a clear message on an older one) |
| `install` | `true` | set to `false` if the job already installed SciProof |
| `python-version` | `3.12` | used when `install` is true |

Outputs: `result` (`PASS`, `FAIL`, `INCONCLUSIVE`, or `ERROR` if SciProof could not run) and `report`
(the path of the JSON report). Inputs reach the shell only through environment variables, never by
string interpolation into the script, so a crafted input cannot inject a command. **`rerun: true`
executes the command in the manifest**: use it only for manifests your own team committed to a
protected branch, never for ones that come from a pull request, and pin the action to a tag or a
commit. The repository's own CI runs the action on a small project (passing, failing policy,
tampered output, and installed from PyPI).

## JUnit output and policies in CI

`sciproof verify --format junit` prints the checks as JUnit XML (a `FAIL` is a failure; an
`INCONCLUSIVE` check is reported as skipped with its reason, and the exit status is still `2`), and
`sciproof list --check --format junit` does the same for every manifest in a directory. Add
`--save-json report.json` to keep the JSON report as an artifact as well. A [policy](POLICY.md) turns
the team's requirements ("signed, clean tree, evidence not older than 30 days") into checks that fail
the build with a reason.

Details: [Security and threat model](../SECURITY.md).
