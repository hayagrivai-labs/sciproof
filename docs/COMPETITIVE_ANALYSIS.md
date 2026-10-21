# Competitive analysis

Audited on **2026-10-19**.

## Scope

Only widely used projects are compared: those with **more than 1,000 GitHub stars** on the audit
date. Smaller projects are left out on purpose, so this page is not a survey of every tool in the
area and says nothing about the ones it does not name.

| Project | Stars (GitHub API) | Version audited | Licence |
| --- | --- | --- | --- |
| [DVC](https://github.com/iterative/dvc) | 15.9k | 3.67.1 | Apache-2.0 |
| [MLflow](https://github.com/mlflow/mlflow) | 28.4k | 3.17.1 | Apache-2.0 |

How to read the claims. Every statement carries a label:

* **[V] verified**: checked by reading the project's metadata or source on the audit date.
* **[D] documented**: stated by the project's own documentation; not independently checked.
* **[A] assumption**: an inference that has not been tested.
* **[U] unverified**: could not be established. Absence of a feature from a page is **not** treated
  as absence of the feature; it is listed as [U].

Pages were partly read through a summarising tool, so details should be re-checked against the
source before they are quoted elsewhere. Nothing here copies code, text or branding from these
projects.

## DVC

* **Purpose** ([docs](https://doc.dvc.org/start/data-pipelines/data-pipelines)) [D]: `dvc.yaml`
  stages list command, dependencies, parameters and outputs; `dvc.lock` records hashes (the example
  shows md5) of dependencies and outputs; `dvc repro` skips unchanged stages and reuses cached
  results. Data versioning, remote storage and caching are its core.
* **Comparing metrics** ([docs](https://doc.dvc.org/command-reference/metrics/diff)) [D]:
  `dvc metrics diff` compares metrics between two Git commits, or a commit and the workspace, as a
  table with `--json`, `--md` and `--precision`. **No tolerance or threshold option is
  documented**, and the page does not say whether a difference changes the exit code [U].
* **Weight [V]:** `pyproject.toml` lists 42 runtime dependencies, including `iterative-telemetry`
  and `dvc-studio-client`. Whether telemetry is on by default was not checked [U].
* **Environment capture [D]:** the pipeline page only recommends a virtualenv; recording of the
  Python/OS environment is not described there [U].

## MLflow

* **Purpose** ([docs](https://mlflow.org/docs/latest/ml/tracking/)) [D]: tracking of parameters,
  code versions, metrics and output files, locally in `mlruns` or on a tracking server, with a UI.
* **Direction [V]:** the repository now describes itself as "the open source AI engineering platform
  for agents, LLMs, and ML models": production observability, evaluation and deployment are the
  centre of the project, not checking whether a rerun reproduces a result.
* **[U]:** artifact hashing, tamper detection and numerical comparison of a rerun were not
  established (a code search through the GitHub API returned no usable result).

## Numeric comparison inside tests

`pytest.approx` and `numpy.testing.assert_allclose` compare numbers with tolerances inside a test
suite you write [A]. They do not record where the reference values came from, which command made
them, or in which environment.

## Feature comparison

`yes` and `no` are [V] for SciProof (tests and [Benchmarks](BENCHMARKS.md)). For the others, the
label in brackets says how sure the entry is.

| | SciProof | DVC | MLflow |
| --- | --- | --- | --- |
| Main job | verify a recorded experiment | version data, run pipelines | track and serve ML/AI runs |
| SHA-256 of declared files, tamper check | yes | md5 by default (D) | ? |
| Directories as inputs/outputs, per-file diff | yes (files named) | yes (A) | ? |
| Opt-in rerun that restores the tree, with timeout | yes | `dvc repro` runs stages (D) | ? |
| Numeric tolerance on rerun outputs | yes (`rtol`/`atol`, JSON/CSV/`.npy`) | none documented (D) | ? |
| Table of metric differences | yes: recorded, found, abs/rel diff, allowed (Markdown, JSON) | `metrics diff` table (D) | UI (A) |
| Explicit verdict | `PASS` / `FAIL` / `INCONCLUSIVE` | none (D) | none (A) |
| Say what was **not** checked | yes, in every report | not described | not described |
| Compare two recorded runs: command, packages, Git, inputs, outputs | yes (`sciproof diff`) | `metrics diff` between commits (D) | compare in UI (A) |
| CI summary output | Markdown, JSON, exit codes | `--md`, `--json` (D) | ? |
| Checkable claims about output values, judged on every verify and rerun | yes (`--claim`) | not described (D) | ? |
| Signature check on the recorded evidence | yes (OpenSSH, `--allowed-signers`) | ? | ? |
| List and batch-verify all recorded runs in a directory | yes (`list --check`) | `dvc status` for a project (A) | UI (A) |
| Export for other tools | in-toto Statement (unsigned) | ? | ? |
| Rules about the evidence itself (age, signed, clean tree, tolerance ceilings), checked on every run | yes (`--policy`) | not described (D) | ? |
| Refresh a baseline from its own recorded declarations, with a diff | yes (`update`) | `dvc repro` re-runs stages (D) | ? |
| JUnit output; a GitHub Action | yes (`--format junit`, `action.yml`) | ? | ? |
| Runtime dependencies | none | 42 (V) | ? |
| Telemetry code or hosted component | none | telemetry dependency present (V); default [U] | optional server (D) |

## Where the others are clearly ahead

* **DVC:** data and model versioning, remote storage, caching and pipelines; a large ecosystem and
  years of production use. SciProof does none of it and should not.
* **MLflow:** experiment tracking at scale, a UI, model registry and serving, LLM tracing and
  evaluation.
* Both have far larger communities and have been tested by many more users. SciProof is alpha
  software and its core idea has not been tested with real users (see the
  [Product roadmap](PRODUCT_ROADMAP.md)).

## Where SciProof does something the audited documentation does not describe

This is a statement about these two projects' documentation on one date, not a claim about
quality in general.

1. A **three-valued verdict with a stated aggregation rule** and a list of what a verdict does
   **not** cover, in every report ([Report](REPORT.md)).
2. **Declared numeric tolerances** for a rerun with stated semantics, and a table of the
   differences (recorded, found, absolute and relative difference, allowed difference) in
   Markdown and JSON ([Numeric comparison](COMPARISON.md)).
3. A **confirmed, timed rerun** that leaves the working tree as it found it and can hand the
   command a minimal environment instead of all of yours (`--clean-env`).
4. **`sciproof diff`**: which of the command, packages, Git state, inputs or outputs changed
   between two recorded runs, without executing anything ([Diff](DIFF.md)).
5. **Claims**: statements such as "accuracy is at least 0.9" that are recorded with the run and
   judged against the recorded output and every rerun, with the limits of what a holding claim
   shows stated in the report ([Claims](CLAIMS.md)).
6. **A rerun that old files cannot satisfy**: the declared outputs are removed (and restored
   afterwards) before the command runs, so a command that never writes its output is reported
   as `FAIL` instead of being compared with its own previous result.
7. **A signature check that trusts only the file you name** ([Signing](SIGNING.md)).
8. **No runtime dependencies, no account, no server, no telemetry.**

## Honest overlap assessment

Hashing the inputs and outputs of a command is **not new**: DVC does it as part of a pipeline
system. If you already use DVC you largely have integrity checking. SciProof's case is the
*verification step* with tolerances and an explicit verdict, for people who do not want a pipeline
system, a server or a container workflow. **This is a hypothesis and must be tested with real
users before any claim of usefulness or uniqueness is made.**

## What SciProof deliberately does not duplicate

* Data and pipeline versioning, remote storage, caching (DVC).
* Experiment dashboards, parameter sweeps, model registries, tracing (MLflow).
* Packaging or rebuilding environments (containers, Nix, conda).
* A new metadata standard; export to an existing one only if users ask.
* Anything cloud-hosted, accounts, blockchain-style "proofs", or AI features.
