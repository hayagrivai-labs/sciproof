# Exit codes

Every command ends with one of these exit codes, so that scripts and CI jobs can act on them.

| Code | Meaning |
| --- | --- |
| `0` | Success. For `verify`: the verdict is `PASS`. |
| `1` | For `update`: the recorded command failed (the failure is recorded). For `list --check`: any manifest `FAIL`s or is invalid. For `diff`: the two manifests differ. For `verify`: the verdict is `FAIL`. For `capture`: the command ran but exited with a nonzero status (the failure is still recorded; use `--allow-nonzero` to exit `0` anyway). |
| `2` | For `verify` and `list --check`: the verdict is `INCONCLUSIVE`. For `update`: the confirmation was declined and nothing ran. For `suggest-inputs`: the audit hook never started, so nothing was observed. |
| `3` | Usage error, invalid or unreadable manifest, or a capture that could not be completed (for example a declared input is missing, or the command could not be started). |

## What the verdicts mean

* `PASS`: every check that was performed succeeded.
* `FAIL`: something differs from the evidence (a modified or missing artifact, a different exit
  code, an output outside the declared tolerance, ...).
* `INCONCLUSIVE`: the evidence is incomplete or a check could not be carried out. Examples: an
  input file is missing, a numeric snapshot is missing, the command cannot be started, the rerun
  timed out, or the rerun was declined. `INCONCLUSIVE` is never reported as success.

See [Concepts](CONCEPTS.md) for what a `PASS` does and does not show.

## Using them in a script

```bash
sciproof verify baseline.sciproof.json
case $? in
  0) echo "evidence holds" ;;
  1) echo "evidence contradicted" ;;
  2) echo "could not decide" ;;
  *) echo "error" ;;
esac
```

With `--json`, the report is printed as one JSON object on stdout; the exit codes stay the same.
Prompts and errors go to stderr.
