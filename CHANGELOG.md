# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Released versions are also published as
[GitHub Releases](https://github.com/hayagrivai-labs/sciproof/releases).

## [Unreleased]

* Policy files with a leading UTF-8 BOM are accepted; the original file bytes remain covered by
  the policy SHA-256 digest.

## [0.3.1] - 2026-10-21

### Changed

* The GitHub Action's description is short enough for the GitHub Marketplace (under 125 characters).

### Added

* `CITATION.cff` (GitHub's "Cite this repository"), a page on showing that results come with evidence
  (`docs/BADGE.md`), and more complete PyPI metadata (keywords, classifiers, documentation links).

## [0.3.0] - 2026-10-21

Nothing here changes how existing manifests are read or written.

### Added

* **Verification policies.** `verify --policy FILE` checks the rules of a strict TOML file against the
  evidence (age of the evidence, exit code, Git state, signature, minimum number of claims, tolerance
  ceilings, rerun and clean-environment requirements) as checks of the report; a rule whose evidence
  is missing is `INCONCLUSIVE`. `sciproof check-policy FILE` validates a policy. An invalid policy is
  rejected before any command is executed. See `docs/POLICY.md`.
* **`sciproof update MANIFEST`**: capture the recorded experiment again with the declarations in the
  manifest (command, inputs, outputs, tolerance, claims, note, hardware), after the same confirmation
  as a rerun, replace the manifest and print what changed. `--dry-run` shows what would happen. The
  declared outputs are removed first and restored if the capture fails. See `docs/UPDATE.md`.
* **A GitHub Action** (`action.yml`): `uses: hayagrivai-labs/sciproof@v0.3.0` verifies a manifest,
  writes the Markdown report to the job summary and exposes `result` and `report` outputs. The
  repository tests it in its own CI. See `docs/CI.md`.
* **JUnit XML** (`verify --format junit`, `list --check --format junit`) for CI test viewers, and
  `verify --save-json FILE` to keep the JSON report next to any other format.
* `sciproof.update_baseline()` and the `policy=` argument of `verify_manifest()` in the Python API.

### Changed

* Installed packages are listed from the `dist-info` directory names instead of parsing every
  METADATA file (about 50 times faster; a capture on an interpreter with 96 packages takes 0.50 s
  instead of 0.63 s). Package names are compared in their standard normal form, so `jaraco.context`
  and `jaraco_context` are one package; manifests recorded by 0.2.x stay comparable.

## [0.2.0] - 2026-10-20

Manifest schema 1.1 is written only when a manifest uses directories, claims, a note or a hardware
record; every other manifest is still 1.0 and is read by 0.1.x. Read **Fixed** first: it changes
what a rerun reports.

### Added

* `capture --note TEXT`: free text stored with the run; shown by `inspect`, `list` and `diff`.
* `capture --record-hardware`: opt-in record of the CPU count, processor and NVIDIA GPUs (via
  `nvidia-smi`); a rerun on different hardware reports the difference (a warning, or
  `INCONCLUSIVE` with `--strict-env`), and `diff` compares it. No host name, user name or
  serial numbers are recorded. Manifest schema 1.1.
* `export --format openlineage`: the run as OpenLineage START and COMPLETE/FAIL events
  (lineage only: no hashes or verdict).
* `sciproof sign MANIFEST --key KEY` and `verify --allowed-signers FILE`: sign a manifest and
  check the signature with OpenSSH (`ssh-keygen -Y`). SciProof implements no cryptography and
  trusts only the allowed-signers file you name; a missing signature is `INCONCLUSIVE`, a wrong
  one `FAIL`. See `docs/SIGNING.md`.
* Claims: `capture --claim OUTPUT 'LOCATION OP VALUE'` records a mechanically checkable statement
  about a value in a `.json` or `.csv` output (`'$.accuracy >= 0.9'`, `~=` with `rtol=`/`atol=`);
  `verify` checks it on the recorded output and on every rerun. Manifest schema 1.1. See
  `docs/CLAIMS.md`.
* `sciproof list [DIR] [-r] [--check]`: the manifests in a directory, newest first; with
  `--check` all of them are verified (integrity only) and the exit status is the worst result.
* `sciproof export MANIFEST`: the manifest as an unsigned in-toto Statement v1 with a SciProof
  predicate. See `docs/LIST_AND_EXPORT.md`.
* `sciproof diff OLD NEW`: what changed between two recorded runs (command, environment and
  packages, Git state, inputs, outputs, exit code), without executing anything. Numeric
  outputs are compared with the baseline tolerance and reported as equivalent or outside it.
  Text, JSON (`sciproof.diff` 1.0) and Markdown formats; `sciproof.diff_manifests()` in
  Python. See `docs/DIFF.md`.
* `verify --format markdown` (and `Report.to_markdown()`): the report as GitHub-flavoured
  Markdown for CI summaries, with a table of numeric differences. `--json` keeps working as
  an alias of `--format json`.
* The JSON report's numeric evidence has `difference_details`: per difference the location,
  recorded and found value, absolute and relative difference and the allowed difference.
* `sciproof suggest-inputs -- COMMAND`: runs a Python command once and lists the files it read
  that are not declared. It exits with `2` (inconclusive) when its audit hook never started
  (the command is not Python, or Python ran with `-I`/`-S`), instead of claiming that nothing
  was missed. The log is hex-encoded so a file name with a line break cannot corrupt it, and
  SciProof's own files (`*.sciproof.json`, `*.sciproof.d/`, rerun directories) are never
  suggested.
* `verify` explains its verdict. The text report ends with a `basis:` line (why the verdict was
  chosen) and `not checked:` lines (what was not established); `verify --json` is now a versioned
  document (`schema: "sciproof.report"`, `schema_version: "1.0"`) with `reason`, `counts`,
  `rerun_executed`, `not_checked` and, per check, structured `evidence` (digests, tolerance rule,
  exact differences). Existing fields are unchanged. See `docs/REPORT.md`.
* `capture` warns when `--rtol` is 1 or more, because such a tolerance accepts values that differ
  by 100% or more.
* CSV differences name the column: `r2c1 (column 'loss'): recorded 0.4, found 0.9`.
* Documentation: exact numeric comparison rules (`docs/COMPARISON.md`), the report format
  (`docs/REPORT.md`), baseline audit, product roadmap, benchmark results and an architecture
  decision record. The competitive analysis covers the widely used tools (more than 1,000
  GitHub stars): DVC and MLflow.
* `benchmarks/run_benchmarks.py`: ten reproducible verification scenarios.
* Tests: report schema and aggregation, and one test per numeric comparison rule.
* `verify --rerun --clean-env` (with `--pass-env NAME`) runs the recorded command with a
  minimal environment instead of a copy of yours; the report lists the passed variable
  names. The confirmation prompt now says which environment the command gets.
* A supported Python API: `capture_experiment`, `verify_manifest`, `load_manifest`,
  `report_to_dict`, `Report`, `Check`, `Status`, `CaptureResult` and the exception types,
  documented in `docs/PYTHON_API.md`, with a runnable `examples/api_usage.py`. The package
  ships a `py.typed` marker. `Report.to_dict()` returns the JSON report as data.

* Directories as inputs and exact outputs (`--input DIR`, `--output DIR`, schema `1.1`): the
  manifest records the sorted list of regular files and a tree digest, and verification and
  `--rerun` name the files that were added, removed or modified. Symbolic links and special
  files inside a declared directory are refused, there is a limit of 10,000 files, and the
  originals of a directory output are restored after a rerun. Manifests without directories
  stay at `1.0`. Closes #5.

### Changed

* A repeated key in a JSON object is now an error in manifests and in numeric output files. It
  was silently resolved to the last value before. SciProof never writes repeated keys, so
  manifests it created are unaffected.
* `SECURITY.md` states that a confirmed rerun inherits the full environment of the calling
  process.

* Declaring a path that is the capture's own manifest or snapshot directory is refused.

### Fixed

* **A rerun could pass without the command writing its outputs.** The previous outputs stayed in
  place while the command ran, so a command that wrote nothing (a bug, a wrong path, an early
  exit) was compared with its own old files and reported as reproduced. The declared outputs
  are now removed before the rerun (they are backed up and restored afterwards), and an output
  the command does not write is reported as `FAIL`: "the rerun did not produce this file".
* `ssh-keygen -Y sign` keeps an existing signature and exits 0 when it cannot ask whether to
  overwrite it; `sciproof sign` now replaces the old signature (and keeps it if signing fails).
* The manifests, snapshots and rerun directories that SciProof writes itself no longer make
  the Git working tree count as "dirty" in the next capture or verification.
* Path names that mean something else on Windows (reserved device names such as `aux.txt`, a
  trailing dot or space, control characters) are refused in manifests, and the directory scan
  refuses them with the same rules, so `capture` reports the file name instead of failing late
  with an "invalid manifest" error.
* Ambiguous JSON (repeated keys) could be accepted as evidence with a different value than other
  parsers would read.
