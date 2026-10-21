# SciProof

[![CI](https://github.com/hayagrivai-labs/sciproof/actions/workflows/ci.yml/badge.svg)](https://github.com/hayagrivai-labs/sciproof/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/sciproof.svg)](https://pypi.org/project/sciproof/)
[![Good first issues](https://img.shields.io/github/issues/hayagrivai-labs/sciproof/good%20first%20issue?color=7057ff&label=good%20first%20issues)](https://github.com/hayagrivai-labs/sciproof/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/hayagrivai-labs/sciproof/blob/main/LICENSE)

Record a computational experiment once, then let anyone **check the evidence**: are the files
unchanged, and does re-running the command give the recorded results, exactly or within a
tolerance you declared?

> **Status: alpha (0.3).** Small, local-first, offline, no runtime dependencies. The idea is
> unproven with real users; see [the competitive analysis](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/COMPETITIVE_ANALYSIS.md) for
> how it overlaps with the most widely used tools (DVC, MLflow).

## The problem

You have a script that produced a table or a figure. Months later, a reviewer, a collaborator
or you yourself ask: *which code and data made this, in which environment, and does it still
come out the same?* SciProof records the answer in a single JSON **evidence manifest** and
can later verify it with an explicit verdict: `PASS`, `FAIL` or `INCONCLUSIVE`.

## What it does and does not prove

| Level | Question | SciProof |
| --- | --- | --- |
| Integrity | Are the files what was recorded? | SHA-256 checks |
| Execution reproducibility | Does the command still produce the recorded results? | opt-in rerun; environment *differences are reported*, not fixed |
| Numerical equivalence | Equal within a declared tolerance? | `--output-numeric` with `rtol`/`atol` for `.json`, `.csv` and (with NumPy) `.npy` |
| **Scientific validity** | **Is the result correct?** | **Not addressed. A reproducible result can still be wrong.** |

Bitwise reproducibility across different hardware or platforms is **not** promised. Details in
[docs/CONCEPTS.md](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/CONCEPTS.md).

## Use cases

SciProof is a lightweight way to verify that a computational experiment's recorded files and
supported outputs have not unexpectedly changed. Typical situations: ML experiment verification,
regression testing for scientific software, numerical simulations, reproducing research results,
data pipeline outputs and benchmark verification. Each one, with its limits, is described in
[docs/USE_CASES.md](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/USE_CASES.md).

## Install

```bash
pipx install sciproof               # or: python -m pip install sciproof   (Python 3.11+)
sciproof --version
```

From source (for development):

```bash
git clone https://github.com/hayagrivai-labs/sciproof.git && cd sciproof
python -m pip install -e ".[dev]"
```

## Quick start

The repository contains a small deterministic experiment (a seeded Monte Carlo estimate of pi).
Run everything below from the repository root, or run `python examples/demo.py` to see the whole
story in a temporary directory.

**1. Capture** the experiment. Declare the files that matter; everything after `--` is the
command (it is *not* run through a shell):

```console
$ sciproof capture --name baseline \
    --input examples/experiment.py --input examples/params.json \
    --output examples/out/results.json \
    --output-numeric examples/out/convergence.csv --rtol 1e-9 \
    -- python examples/experiment.py
Evidence written to baseline.sciproof.json
  command exit code: 0
  inputs recorded:   2
  outputs recorded:  2
Note: stdout/stderr are stored in the manifest; check them for secrets before sharing it.
```

**2. Inspect** the evidence. This never runs the recorded command:

```console
$ sciproof inspect baseline.sciproof.json
SciProof manifest: baseline
  format:      sciproof.manifest 1.0
  captured:    2026-10-17T18:47:12+00:00

Command (recorded, NOT executed by 'inspect'):
  python examples/experiment.py
...
Outputs (2):
  examples/out/results.json  sha256:723d792e1b1ad975...  69 bytes  compare: exact hash
  examples/out/convergence.csv  sha256:131eb2551a942bb9...  89 bytes  compare: numeric (rtol=1e-09, atol=0.0)
```

**3. Verify integrity** (nothing is executed):

```console
$ sciproof verify baseline.sciproof.json
  [PASS] input examples/experiment.py: unchanged (SHA-256 matches)
  [PASS] input examples/params.json: unchanged (SHA-256 matches)
  [PASS] output examples/out/results.json: unchanged (SHA-256 matches)
  [PASS] output examples/out/convergence.csv: unchanged (SHA-256 matches)

  note: scope: artifact integrity only. The recorded command was NOT executed, so this does not show that the results can be reproduced.

  basis: PASS because all 4 performed checks succeeded; this covers only the checks listed above, not what is under 'not checked'
  not checked: reproducibility: the recorded command was not executed
  not checked: scientific validity: a matching result is not shown to be correct
  not checked: files that were not declared as inputs or outputs
  not checked: authenticity: manifests are unsigned, so who produced this evidence is not verified

RESULT: PASS
```

**4. Verify by re-execution.** Running the command is a deliberate act: it needs `--rerun` and
either an interactive confirmation or `--yes`.

```console
$ sciproof verify baseline.sciproof.json --rerun --yes
  ...
  [PASS] exit code: 0, same as recorded
  [PASS] rerun output examples/out/results.json: identical to the recorded output (exact SHA-256)
  [PASS] rerun output examples/out/convergence.csv: numerically equivalent (rtol=1e-09, atol=0.0)
RESULT: PASS
```

**5. A modified output is detected:**

```console
$ sciproof verify baseline.sciproof.json
  [FAIL] output examples/out/results.json: modified since capture (SHA-256 differs)
  basis: FAIL because 1 (output examples/out/results.json) check(s) contradict the evidence; one failed check is enough
  ...
RESULT: FAIL
```

Exit codes: `0` PASS, `1` FAIL, `2` INCONCLUSIVE, `3` usage error or invalid manifest. A missing
input, a missing numeric snapshot, a command that cannot be started, a timeout or a declined
rerun are `INCONCLUSIVE`, never `PASS`.

Every report says why the verdict was chosen (`basis`) and what was *not* checked. With `--json`
it also carries the evidence behind each check (digests, tolerances, the exact differences).
Details: [docs/REPORT.md](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/REPORT.md);
the exact numeric comparison rules:
[docs/COMPARISON.md](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/COMPARISON.md).

A walkthrough with NumPy and pandas, including how to choose tolerances: [docs/TUTORIAL_NUMPY_PANDAS.md](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/TUTORIAL_NUMPY_PANDAS.md).

## Commands

```text
sciproof capture --name NAME [--input P]... [--output P]... [--output-numeric P]...
                 [--rtol X] [--atol Y] [--timeout S] [--max-log-bytes N]
                 [--claim OUTPUT 'LOCATION OP VALUE']... [--note TEXT] [--record-hardware]
                 [--force] [--allow-nonzero]
                 -- COMMAND [ARGS...]
sciproof verify  MANIFEST [--rerun] [--yes] [--strict-env] [--clean-env]
                 [--allowed-signers FILE] [--policy FILE]
                 [--format text|json|markdown|junit] [--save-json FILE]
                 [--pass-env NAME]... [--timeout S] [--json]
sciproof inspect MANIFEST
sciproof diff    OLD NEW [--format text|json|markdown]
sciproof update  MANIFEST [--yes] [--dry-run] [--note TEXT]
sciproof check-policy FILE
sciproof sign    MANIFEST --key KEY
sciproof list    [DIR] [-r] [--check] [--format text|json|markdown]
sciproof export  MANIFEST [--format in-toto|openlineage] [-o FILE]
sciproof suggest-inputs [--input P]... [--output P]... -- COMMAND [ARGS...]
```

* Paths must be inside the current directory; the manifest is written there as
  `NAME.sciproof.json`. Numeric outputs are also copied to `NAME.sciproof.d/` (needed to compare
  with a tolerance; keep it next to the manifest).
* Declared inputs must be read-only for the command; capture fails if the command changes one.
* `--input` and `--output` accept a directory: every regular file below it is recorded, and a
  verification names the files that were added, removed or modified. Symbolic links inside
  are refused, empty directories are not recorded, and there is a limit of 10,000 files
  ([details](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/MANIFEST.md#directories)).
  `--output-numeric` takes single files only.
* A command that exits with a nonzero status is still recorded (capture then exits with `1`
  unless `--allow-nonzero`).
* `--json` prints the verification report as a single JSON object (same exit codes), for CI
  and scripts. Prompts and errors go to stderr, so stdout stays valid JSON.
* `suggest-inputs` runs a Python command once (it really runs, with side effects) and lists files it
  opened for reading that are not declared, so you can add them as `--input`. It only sees reads made
  by Python code; C extensions, other programs, network and environment variables are invisible, so
  an empty answer is not proof. If the hook never starts (the command is not Python, or Python ran
  with `-I` or `-S`), it says so and exits with `2` instead of reporting "no undeclared files".
  Delete stale `__pycache__` before a rerun: Python reuses a `.pyc` when the source's size and
  modification second are unchanged.
* `--strict-env` makes a rerun `INCONCLUSIVE` (without executing) if Python version, platform
  or recorded package versions differ.
* `--format markdown` prints the verification report as GitHub-flavoured Markdown for CI
  summaries (`sciproof verify ... --format markdown >> "$GITHUB_STEP_SUMMARY"`), including a
  table of numeric differences (recorded, found, absolute and relative difference, allowed).
* `diff` compares two manifests and lists what changed between two recorded runs: command,
  environment and packages, Git state, inputs, outputs (numeric ones with the baseline
  tolerance) and exit code. It executes nothing. Exit status `0` if nothing differs, `1`
  otherwise ([details](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/DIFF.md)).
* `--claim OUTPUT 'LOCATION OP VALUE'` records a statement about one value in a `.json` or
  `.csv` output (`'$.accuracy >= 0.9'`, `'$.loss ~= 0.25 atol=0.01'`) that `verify` checks on the
  recorded output and on every rerun. It shows that the value satisfies the comparison, nothing
  more ([details](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/CLAIMS.md)).
* `--note TEXT` stores free text with the run (why it was made); `--record-hardware` also records
  the CPU count, processor and NVIDIA GPUs (no host name, user name or serial numbers), and a
  rerun on different hardware says so.
* `sign` and `verify --allowed-signers FILE` sign a manifest and check the signature with
  OpenSSH (`ssh-keygen -Y`); SciProof implements no cryptography and trusts only the file you
  name ([details](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/SIGNING.md)).
* `list` shows the manifests in a directory, newest first; `list --check` verifies all of them
  (integrity only) and exits with the worst result. `export` writes a manifest as an unsigned
  in-toto Statement or as OpenLineage run events ([details](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/LIST_AND_EXPORT.md)).
* `--policy FILE` checks a TOML file of rules about the evidence itself (not older than N days,
  clean Git tree, signed, at least one claim, no tolerance looser than a ceiling, rerun with
  `--clean-env`, ...); each rule is a check of the report, and an unmet requirement is
  `INCONCLUSIVE`, not a pass ([details](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/POLICY.md)).
* `update` captures the recorded experiment again with the declarations already in the manifest
  and shows what changed; it executes the recorded command, so it asks for confirmation
  ([details](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/UPDATE.md)).
* In CI: `--format junit` for test viewers, and a GitHub Action (`uses: hayagrivai-labs/sciproof@v0.3.0`)
  that verifies a manifest and writes the report to the job summary
  ([details](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/CI.md)).
* `--clean-env` gives a rerun only a minimal environment (`PATH`, temp directories and the
  numerics-related variables SciProof records) instead of all of yours, so a manifest from
  someone else cannot read your credentials from it. `--pass-env NAME` adds a variable. It
  limits environment variables only: the command can still read your files and use the
  network, so it is not a sandbox.

Python API: [docs/PYTHON_API.md](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/PYTHON_API.md).

Manifest format: [docs/MANIFEST.md](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/MANIFEST.md).

## Threat model and limitations

* A manifest contains a command and must be treated as untrusted. `inspect` and plain `verify`
  never execute it; `--rerun` runs it **with your permissions, unsandboxed**. Only rerun
  manifests you trust, ideally in a disposable VM or container. See [SECURITY.md](https://github.com/hayagrivai-labs/sciproof/blob/main/SECURITY.md).
* Manifests are **not signed** (a tested recipe using `ssh-keygen -Y` is in
  [docs/DESIGN_SIGNING.md](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/DESIGN_SIGNING.md)): hashes detect changes, they do not stop someone who can rewrite
  both manifest and files from fabricating consistent evidence.
* Only declared files are tracked. Undeclared inputs, network, time and system libraries are
  invisible. SciProof reports environment differences but does not recreate environments.
* stdout/stderr and the command line are stored in the manifest; review them for secrets.
* Numeric comparison supports `.json` and `.csv` (standard library only) and `.npy` arrays
  (`pip install 'sciproof[numpy]'`; numeric, boolean and complex dtypes, never unpickled).
* The environment is probed from the command's interpreter only if the command is a Python
  interpreter; otherwise the interpreter running SciProof is described (and the manifest says so).
* Linux, Windows and macOS are tested in CI (Python 3.11-3.13).
* The unit tests that check symlink escapes need symlink support (they run in Linux CI and are
  skipped where symlinks are unavailable).

## Related tools

| Tool | Relation |
| --- | --- |
| [DVC](https://doc.dvc.org/start/data-pipelines/data-pipelines) | Pipelines with hashes of dependencies and outputs; heavier, more powerful. If you already use it you largely have integrity checking. |
| [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/) | Experiment tracking for ML with parameters and metrics, optional UI. |

Full analysis, with what could not be confirmed: [docs/COMPETITIVE_ANALYSIS.md](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/COMPETITIVE_ANALYSIS.md).

## Roadmap (decided by feedback, not by speculation)

Nothing below is promised. Each item is only worth doing if real users ask for it:

1. Try it on real experiments of a few researchers; record what is awkward or missing.
2. Based on that: more numeric formats (for example `.npz`), directory outputs, or a manifest export to RO-Crate
   for interoperability.
3. Signing or timestamping of manifests, if integrity against a hostile party matters to users.

## Contributing

New here? The open issues are written for newcomers: each one has the problem, the files to touch
and the acceptance criteria. Start with the [good first issues](https://github.com/hayagrivai-labs/sciproof/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22),
or ask in [Discussions](https://github.com/hayagrivai-labs/sciproof/discussions). A map of the code is in
[docs/ARCHITECTURE.md](https://github.com/hayagrivai-labs/sciproof/blob/main/docs/ARCHITECTURE.md).

See [CONTRIBUTING.md](https://github.com/hayagrivai-labs/sciproof/blob/main/CONTRIBUTING.md). Quick checks: `ruff check .`, `ruff format --check .`,
`python -m pytest`, `python examples/demo.py`.

## Licence

[MIT](https://github.com/hayagrivai-labs/sciproof/blob/main/LICENSE): short and permissive, so researchers and companies can use and embed it
without legal review.
