# What SciProof can and cannot tell you

"Reproducible" is used for several different claims. SciProof keeps them apart, because
confusing them is how tools end up promising more than they deliver.

| Level | Question | What SciProof does |
| --- | --- | --- |
| 1. Integrity | Are the files exactly what was recorded? | `verify` compares SHA-256 hashes of declared inputs, outputs and numeric snapshots. Strong and cheap. Detects modification and deletion; does not say anything about *how* the files were made. |
| 2. Environment and execution reproducibility | Does running the recorded command again produce the recorded result? | `verify --rerun` executes the command once and compares exit code and outputs. It records and *compares* Python version, platform and installed package versions, and reports differences as warnings (or `INCONCLUSIVE` with `--strict-env`). It does **not** recreate the environment. |
| 3. Numerical equivalence | Are results the same within an explicitly declared tolerance? | Outputs declared with `--output-numeric` (`.json`, `.csv`, `.tsv`, and `.npy` with NumPy) are compared value by value with `rtol`/`atol`. The tolerance is chosen by you at capture time and is stored in the manifest. SciProof cannot tell you what tolerance is scientifically appropriate. |
| 4. Scientific validity | Is the result *correct*, is the model appropriate, is the conclusion justified? | **Nothing.** No tool that re-runs code can answer this. A deterministic program that implements a wrong model reproduces its wrong answer perfectly. |

A `PASS` is therefore only ever a statement about levels 1 to 3, limited to the checks listed
in the report. The report says which checks were *not* run (for example, "the command was NOT
executed").

## Verdicts

* `PASS`: all checks that were performed succeeded.
* `FAIL`: something differs from the evidence (modified/missing artifact, different exit code,
  output outside tolerance, ...).
* `INCONCLUSIVE`: the evidence is incomplete or a check could not be carried out (an input is
  unavailable, the command cannot be started, the rerun timed out or was declined, a numeric
  snapshot is missing). `INCONCLUSIVE` is never treated as success and has its own exit code.

Exit codes: `0` PASS, `1` FAIL, `2` INCONCLUSIVE, `3` usage error or invalid manifest.

## Limits of re-execution

Exact (bitwise) agreement is **not** promised across different hardware, operating systems or
library builds. Typical reasons a faithful rerun differs:

* Floating-point behaviour: different CPU instruction sets, compiler flags, BLAS/LAPACK builds,
  fused multiply-add, or the order of parallel reductions.
* Different Python or package versions (reported, but not reinstalled).
* Unseeded randomness, time, network access, hostnames, and anything else the command reads
  that is not a declared input.
* GPU nondeterminism.
* System libraries and data outside the declared inputs.

SciProof only knows about files you declare. If the experiment reads an undeclared file,
changes to it are invisible. If the recorded command is not a Python interpreter, the
recorded Python version and packages describe the interpreter that ran SciProof (the manifest
says so in `environment.source`).

## What a rerun does to your directory

`verify --rerun` runs the command in the manifest's directory. Before running, existing
declared outputs are copied to a temporary directory and **removed**, so that the command starts
without them: an old file must not be able to "reproduce" a result that the command never wrote
(an output the command does not produce is reported as `FAIL`: "the rerun did not produce this
file"). A command that skips work when its output already exists will therefore do the work
again. Afterwards the files produced by the
rerun that differ from the originals are moved to `.sciproof-rerun-<timestamp>/` (nothing is
moved, and no directory is created, if the rerun did not change an output) and your original outputs are restored, so
checking evidence never overwrites the evidence. The command itself can still do anything the
current user can do. See [SECURITY.md](../SECURITY.md).
