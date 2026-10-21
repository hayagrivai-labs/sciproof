# Benchmarks

Ten verification situations, run end to end through the SciProof command line in fresh
temporary directories. Reproduce with `python benchmarks/run_benchmarks.py` (add `--json FILE` for
the raw numbers and `--repeat N` for the overhead table). The script exits with `1` if SciProof
gives an outcome other than the expected one, so it also works as a smoke test.

## What this does and does not show

* **Measured:** the verdict or exit code SciProof gives in each situation, and the wall-clock time
  of whole command invocations.
* **Not measured:** memory, large files, many runs, other operating systems, report clarity as
  judged by users, setup effort beyond the commands in the script.
* **Bias to keep in mind:** the "expected" outcomes were defined by the SciProof authors from
  SciProof's documented semantics. A run where every outcome matches shows that SciProof does what
  it documents, not that the documented behaviour is what everyone wants.
* **Not compared:** [DVC and MLflow](COMPETITIVE_ANALYSIS.md) were not run. Their workflows are not
  equivalent tasks (pipelines and data versioning; a tracking server and UI), so no numbers are
  reported for them and no claim of speed or reliability against them is made.

## Conditions of the recorded run

* 2026-10-19, Windows 11, Python 3.13.1, one machine, nothing else running.
* SciProof as of the commit that introduced `sciproof diff` and `verify --format markdown`.
* The experiment reads `data.json`, writes `result.json` (a value that can be nudged through an
  environment variable) and takes about 0.1 s. Times are of the whole CLI invocation, including
  interpreter start-up.

## Scenario results

`Observed` is derived from the exit code: `0` PASS, `1` FAIL, `2` INCONCLUSIVE, `3` error.

| Scenario | Expected | Observed |
| --- | --- | --- |
| 1. Unchanged deterministic experiment, integrity check | PASS | PASS |
| 1b. Same, verified by re-execution | PASS | PASS |
| 2. Modified input file | FAIL | FAIL |
| 3. Modified output file | FAIL | FAIL |
| 4. Numeric output within the tolerance (rerun, `eps=1e-9`, `rtol=1e-6`) | PASS | PASS |
| 5. Numeric output outside the tolerance (rerun, `eps=1e-2`) | FAIL | FAIL |
| 6. Missing required evidence (an input file deleted) | INCONCLUSIVE | INCONCLUSIVE |
| 7a. Truncated manifest | error | error (exit 3) |
| 7b. Manifest with a repeated key | error | error (exit 3) |
| 7c. Manifest hash edited to a wrong value | FAIL | FAIL |
| 8a. Command fails during capture | FAIL, evidence written | exit 1, evidence written |
| 8b. Capture exceeds a timeout | error, no evidence | exit 3, no evidence written |
| 9. Rerun whose command never writes the output, with the old output still on disk | FAIL | FAIL ("the rerun did not produce this file") |
| 10. A recorded claim (`$.value < 40`) that does not hold for the value 42 | FAIL | FAIL |

All 14 matched the expectation. Scenario 9 is the one that found a real defect: before the fix, the old
output stayed in place while the command ran and the rerun was reported as `PASS` (reproduced by hand
on the previous code; the benchmark itself was added with the fix).

## Overhead

Median of 7 runs of one cycle in a fresh directory, in seconds:

| Step | Seconds |
| --- | --- |
| The experiment alone | 0.121 |
| `sciproof capture` | 0.701 |
| `sciproof verify` (integrity only) | 0.225 |
| `sciproof verify --rerun --yes` | 0.689 |

Reading the numbers honestly: for a 0.1 s experiment, `capture` adds about half a second. Timing
its parts separately on this machine (median of 5): about 0.21 s for the child interpreter that
lists the installed packages, 0.08 s for the three `git` calls and 0.08 s to import SciProof, on
top of the experiment and process start-up (an empty child Python takes 0.06 s). This is a roughly
fixed cost, not a measure of scalability, and the package listing grows with the number of
installed packages. The package listing could run concurrently with the command, but it must
describe the environment *before* the command ran (a command may install packages), so it is
not overlapped. Integrity verification costs about one interpreter start-up plus hashing the
declared files.

## Two attempts to make `capture` faster

* **Running the probes concurrently** (interpreter packages, `git`, hashing) gave no gain: median
  0.513 s on `main` against 0.529 s with threads, inside the noise (9 runs each). It was not kept.
* **Listing packages from the `dist-info` directory names** instead of parsing every METADATA file
  does help where it matters. Timed separately, listing 95 packages took 224 ms before and 4 ms
  after; a whole `capture` on an interpreter with 96 packages took 0.632 s before and 0.498 s after
  (median of 9). With a small environment (34 packages) the listing was already fast and there is
  no measurable difference. The result is checked to give the same names and versions as the exact
  way, and the exact way is still used for anything the directory name does not describe.

## What the benchmark found

* Before the strict-JSON change a repeated key in a manifest was accepted and the last value won
  (shown with `schema_version` in a probe before the fix; the benchmark itself was not run against
  the old code). Scenario 7b now ends in an error.
* The numbers show the fixed cost of a capture (about 0.6 s) is the main thing to improve if
  experiments are very short.
