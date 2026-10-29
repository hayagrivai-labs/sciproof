# Use cases

SciProof does one narrow thing: it records a computational experiment (command, declared input
and output files, environment) in an evidence manifest, and later checks that evidence. The
verdict is `PASS`, `FAIL` or `INCONCLUSIVE`.

> **The core promise:** verify that a computational experiment's recorded files and supported
> outputs have not unexpectedly changed.

That promise has three parts:

- **Integrity verification:** have the declared input or output files changed?
- **Repeatability checks:** does rerunning a trusted experiment produce comparable outputs?
- **Numerical comparison:** are the new results within the tolerance you declared?

The scenarios below illustrate how the documented features fit real situations. They are not
testimonials: SciProof is alpha software and the idea is not yet proven with real users. See
[Concepts](CONCEPTS.md) for what a verdict does and does not mean.

## 1. AI/ML experiment verification

**Problem.** A model's accuracy, loss or evaluation metric changes, and you need to find out
whether the code, the input files or the outputs changed.

**How SciProof helps.** `capture` hashes the declared inputs and outputs. `verify` shows which
file differs, and `verify --rerun` re-executes the command and compares `.json`, `.csv` and
`.npy` metrics exactly or within a declared `--rtol` / `--atol`.

**Who.** Machine learning engineers, computer vision and NLP researchers, independent AI
developers.

This is the closest fit to the current feature set. See the
[NumPy & pandas tutorial](TUTORIAL_NUMPY_PANDAS.md) for choosing tolerances, or the
[shell pipeline tutorial](TUTORIAL_SHELL_PIPELINE.md) for a non-Python experiment.

## 2. Regression testing for scientific software

**Problem.** You optimise a formula, an algorithm or a data-processing function, and the change
unexpectedly alters earlier outputs.

**How SciProof helps.** Capture a small deterministic run before the change, then verify after
it. Outputs that moved are reported as `FAIL`; harmless floating-point noise stays inside the
tolerance you wrote down.

**Who.** Maintainers of open-source scientific libraries, numerical algorithm developers.

Small deterministic cases are the easiest to reproduce, so this is a good first scenario to try.

## 3. Numerical simulations

**Problem.** You update a simulation or numerical algorithm and its output values shift.

**How SciProof helps.** Verifies the recorded input and output files and compares supported
numeric results within configurable tolerances.

**Who.** Computational physics researchers, engineering simulation teams, numerical
optimisation developers.

## 4. Reproducing research results

**Problem.** Weeks or months after an experiment, you want to know whether rerunning it gives
comparable results.

**How SciProof helps.** The manifest records the command, the file hashes and the environment.
`verify` checks the artifacts; `verify --rerun` compares a new run with the recorded outputs and
reports environment differences (Python version, platform, packages).

**Who.** Research software developers, collaborators, computational research teams.

**Limitation.** SciProof reports environment differences but does not recreate the software
environment, so identical results on a different machine are not guaranteed.

## 5. Data pipeline output verification

**Problem.** You change a Python data-processing pipeline and its output files change; you need
to know whether the change was expected.

**How SciProof helps.** Checks the integrity of selected files and compares supported
structured outputs (`.json`, `.csv`).

**Who.** Data engineers, analytics developers, Python automation teams.

**Limitation.** SciProof is not a data-lineage, dataset-versioning or pipeline-orchestration
platform, and only the files you declare are tracked.

## 6. Model evaluation and benchmark verification

**Problem.** A new model version produces different benchmark results, and you need to check
whether the evaluation inputs or the recorded outputs changed.

**How SciProof helps.** Compares supported result files and highlights differences between the
recorded and the new outputs. `verify --json` and the exit codes make it easy to run in CI; see
[Using SciProof in CI](CI.md) and [Exit codes](EXIT_CODES.md).

**Who.** Model evaluation teams, benchmark maintainers, AI tooling developers.

## 7. Concise verification in CI

**Problem.** CI logs contain more output than needed when a workflow only needs a clear verification verdict, while failure diagnostics must remain available for debugging.

**How SciProof helps.** Use `-q` or `--quiet` with `sciproof verify` to suppress decorative headers and print a concise verdict to stdout. Failure diagnostics are sent to stderr, and the command's exit code is preserved.

For example, a GitHub Actions step can run:

```yaml
      - name: Verify the evidence (quiet mode)
        run: sciproof verify --quiet baseline.sciproof.json
```

For a shell script that checks multiple manifests, use:

```bash
for manifest in *.sciproof.json; do
    [ -e "$manifest" ] || continue
    echo "Checking $manifest"
    sciproof verify --quiet "$manifest" || exit $?
done
```

The loop identifies each manifest and stops when a verification command returns a nonzero exit code. Quiet mode changes the output, not the verification result. Failure diagnostics remain available on stderr, and the exit status can be used by CI to determine whether the job should fail.

These examples perform integrity verification; they do not execute the experiment recorded in a manifest. See [Using SciProof in CI](CI.md) and [Exit codes](EXIT_CODES.md) for more details.

## Where SciProof fits among other tools

Tools such as DVC already cover parts of experiment tracking and reproducibility. SciProof does
not try to be a full experiment-tracking platform. It aims to stay simple, local-first and
focused on verification: no account, no server, no runtime dependencies. The
[comparison](COMPETITIVE_ANALYSIS.md) describes the overlaps in detail.

## Try it on your own experiment

Take one real Python experiment and test three scenarios:

1. All recorded files remain unchanged: expect `PASS`.
2. An input or output file is modified: expect `FAIL`.
3. A rerun produces a small numerical difference, then a larger one: expect `PASS` while the
   difference is inside the tolerance and `FAIL` once it is outside.

If a check cannot be carried out (for example a declared file is missing, or the rerun times
out), the verdict is `INCONCLUSIVE`, never success. If SciProof would (or would not) solve a
problem in your workflow, please tell us in an
[issue](https://github.com/hayagrivai-labs/sciproof/issues): the roadmap is decided by feedback.
