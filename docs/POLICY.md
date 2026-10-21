# Verification policies

A verification answers "does this evidence still hold?". A **policy** answers a different question,
the one a team or a CI job actually has: "is this evidence *good enough for us*?" It writes down,
once and in a file you can review, what the evidence must look like, and `verify` checks it every
time.

```bash
sciproof check-policy sciproof.policy.toml          # validate a policy and list its rules
sciproof verify baseline.sciproof.json --policy sciproof.policy.toml
```

Every rule becomes one check of the report (`policy manifest.max_age_days`, ...), so a policy
failure is a `FAIL` with a reason, in the same text, Markdown, JUnit and JSON reports as everything
else. The report names the policy file and its SHA-256, so a reviewer can see which rules applied.

## The file

A small TOML file; an annotated example is in
[`examples/sciproof.policy.toml`](https://github.com/hayagrivai-labs/sciproof/blob/main/examples/sciproof.policy.toml).

```toml
version = 1

[manifest]
max_age_days = 30          # the evidence was captured at most 30 days ago
require_exit_code = 0      # the recorded run exited with this status
require_git = true         # a Git commit was recorded
forbid_dirty_git = true    # ... and the working tree had no uncommitted changes
require_signature = true   # the manifest is signed (needs --allowed-signers)
min_claims = 1             # at least one claim is recorded

[outputs]
require_numeric = ["metrics.json"]   # compared with a tolerance, not by exact hash
max_rtol = 1e-3                      # no numeric output may declare a looser tolerance
max_atol = 1e-6

[rerun]
require = true             # the verification must include a rerun (--rerun)
require_clean_env = true   # ... with a minimal environment (--clean-env)
```

## Rules

| Rule | `PASS` when | `FAIL` when | `INCONCLUSIVE` when |
| --- | --- | --- | --- |
| `manifest.max_age_days` | captured at most N days ago | older, or dated in the future | |
| `manifest.require_exit_code` | the recorded run exited with N | it exited with another status | |
| `manifest.require_git` | a commit was recorded | no Git state was recorded | |
| `manifest.forbid_dirty_git` | the tree was clean | it had uncommitted changes | no Git state was recorded |
| `manifest.require_signature` | the signature is good | the signature is bad | no `--allowed-signers` was given, or it could not be checked |
| `manifest.min_claims` | at least N claims | fewer | |
| `outputs.require_numeric` | each listed output is compared with a tolerance | it is exact, or not declared | |
| `outputs.max_rtol`, `max_atol` | every numeric output is within the ceiling (or there is none) | an output declares a looser tolerance | |
| `rerun.require` | the command was re-executed | | the verification ran without `--rerun`, or the rerun did not happen |
| `rerun.require_clean_env` | the rerun got a minimal environment | it got the whole environment | there was no rerun |

A rule whose evidence is missing is `INCONCLUSIVE`, never a pass: for example a policy that requires
a signature, verified without `--allowed-signers`, cannot say anything about it. The verdict follows
the usual rule: any `FAIL` makes the result `FAIL`, otherwise any `INCONCLUSIVE` prevents `PASS`.

## Strict by design

A policy that is silently misread is worse than none, so the file is validated before anything
runs: an unknown section or rule, a value of the wrong type (`max_age_days = "30"`), a number out of
range (`max_age_days = 0`, a negative tolerance, `nan`), a list with an unsafe or repeated path, a
missing or different `version`, invalid TOML, or a file larger than 64 KiB is an error that names the
rule. With `--rerun`, a bad policy is rejected **before** the recorded command is executed.

## What a policy does not do

It states which evidence you require; it does not say that the experiment is correct, and a manifest
cannot choose or weaken its policy (the file comes from the command line). Rules about the *age* of
the evidence use the clock of the machine that verifies. `sciproof check-policy` only validates the
file; it verifies nothing.

## Python

```python
report = sciproof.verify_manifest("baseline.sciproof.json", policy="sciproof.policy.toml")
print(report.policy)  # {'file': 'sciproof.policy.toml', 'sha256': '...', 'version': 1}
```
