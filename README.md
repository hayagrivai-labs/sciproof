# SciProof

[![CI](https://github.com/hayagrivai-labs/sciproof/actions/workflows/ci.yml/badge.svg)](https://github.com/hayagrivai-labs/sciproof/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Record a computational experiment once, then let anyone **check the evidence**: are the files
unchanged, and does re-running the command give the recorded results, exactly or within a
tolerance you declared?

> **Status: alpha (0.1).** Small, local-first, offline, no runtime dependencies. The idea is
> unproven with real users; see [the competitive analysis](docs/COMPETITIVE_ANALYSIS.md) for
> how it overlaps with existing tools (DVC, ReproZip, Sumatra, MLflow, RO-Crate).

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
[docs/CONCEPTS.md](docs/CONCEPTS.md).

## Install

```bash
git clone https://github.com/hayagrivai-labs/sciproof.git && cd sciproof
python -m pip install -e .          # Python 3.11+
sciproof --version
```

(Not published on PyPI yet.)

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
RESULT: FAIL
```

Exit codes: `0` PASS, `1` FAIL, `2` INCONCLUSIVE, `3` usage error or invalid manifest. A missing
input, a missing numeric snapshot, a command that cannot be started, a timeout or a declined
rerun are `INCONCLUSIVE`, never `PASS`.

A walkthrough with NumPy and pandas, including how to choose tolerances: [docs/TUTORIAL_NUMPY_PANDAS.md](docs/TUTORIAL_NUMPY_PANDAS.md).

## Commands

```text
sciproof capture --name NAME [--input P]... [--output P]... [--output-numeric P]...
                 [--rtol X] [--atol Y] [--timeout S] [--max-log-bytes N]
                 [--force] [--allow-nonzero] -- COMMAND [ARGS...]
sciproof verify  MANIFEST [--rerun] [--yes] [--strict-env] [--timeout S] [--json]
sciproof inspect MANIFEST
```

* `--input` and `--output` accept files and directories. A directory is recorded as a sorted
  list of its files (see [docs/MANIFEST.md](docs/MANIFEST.md)); symlinks inside it are refused.
* Paths must be inside the current directory; the manifest is written there as
  `NAME.sciproof.json`. Numeric outputs are also copied to `NAME.sciproof.d/` (needed to compare
  with a tolerance; keep it next to the manifest).
* Declared inputs must be read-only for the command; capture fails if the command changes one.
* A command that exits with a nonzero status is still recorded (capture then exits with `1`
  unless `--allow-nonzero`).
* `--json` prints the verification report as a single JSON object (same exit codes), for CI
  and scripts. Prompts and errors go to stderr, so stdout stays valid JSON.
* `--strict-env` makes a rerun `INCONCLUSIVE` (without executing) if Python version, platform
  or recorded package versions differ.

Manifest format: [docs/MANIFEST.md](docs/MANIFEST.md).

## Threat model and limitations

* A manifest contains a command and must be treated as untrusted. `inspect` and plain `verify`
  never execute it; `--rerun` runs it **with your permissions, unsandboxed**. Only rerun
  manifests you trust, ideally in a disposable VM or container. See [SECURITY.md](SECURITY.md).
* Manifests are **not signed** (a tested recipe using `ssh-keygen -Y` is in
  [docs/DESIGN_SIGNING.md](docs/DESIGN_SIGNING.md)): hashes detect changes, they do not stop someone who can rewrite
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
| [ReproZip](https://docs.reprozip.org/en/1.x/) | Packs a Linux experiment with its files and dependencies so it can be rerun elsewhere. SciProof does not package anything. |
| [Sumatra](https://sumatra.readthedocs.io/en/latest/introduction.html) | Electronic-lab-notebook style run records. |
| [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/) | Experiment tracking for ML with parameters and metrics, optional UI. |
| [RO-Crate](https://www.researchobject.org/ro-crate/specification/1.1/introduction.html) | A standard to *describe* research outputs; does not verify them. Mapping study: [docs/DESIGN_RO_CRATE.md](docs/DESIGN_RO_CRATE.md). |

Full analysis, with what could not be confirmed: [docs/COMPETITIVE_ANALYSIS.md](docs/COMPETITIVE_ANALYSIS.md).

## Roadmap (decided by feedback, not by speculation)

Nothing below is promised. Each item is only worth doing if real users ask for it:

1. Try it on real experiments of a few researchers; record what is awkward or missing.
2. Based on that: more numeric formats (for example `.npz`), directory outputs, or a manifest export to RO-Crate
   for interoperability.
3. Signing or timestamping of manifests, if integrity against a hostile party matters to users.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Quick checks: `ruff check .`, `ruff format --check .`,
`python -m pytest`, `python examples/demo.py`.

## Licence

[MIT](LICENSE): short and permissive, so researchers and companies can use and embed it
without legal review.
