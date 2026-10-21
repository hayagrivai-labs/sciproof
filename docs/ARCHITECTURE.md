# Architecture for contributors

SciProof is a small package (`src/sciproof/`) with no runtime dependencies. The command line is a thin
layer over a Python API; every feature has a function you can call from a test.

## The flow

```
capture:   cli.capture -> capture.py (run the command, hash inputs and outputs, probe the environment)
                       -> manifest.py (build, validate, write NAME.sciproof.json)
verify:    cli.verify  -> manifest.py (load and validate; untrusted input)
                       -> verify.py (checks -> Report) -> policy.py (extra checks) -> report output
```

Everything that reads a manifest treats it as **untrusted**: paths are validated (`paths.py`), JSON is
read strictly (`jsonstrict.py`), and nothing is executed unless `--rerun` or `update` is used.

## Modules

| Module | What it does |
| --- | --- |
| `cli.py` | argparse commands and exit codes; calls the functions below, prints nothing clever |
| `capture.py` | runs the command once and records evidence |
| `verify.py` | turns a manifest into a `Report` of checks (`PASS` / `FAIL` / `INCONCLUSIVE`); optional rerun |
| `manifest.py` | the manifest schema: build, validate, read and write |
| `policy.py` | strict TOML policies; each rule becomes a check of the report |
| `update.py` | `sciproof update`: capture again from the manifest's own declarations, then diff |
| `numeric.py` | comparison of `.json`, `.csv`, `.tsv` and `.npy` outputs with tolerances |
| `claims.py` | claims such as `$.accuracy >= 0.9` evaluated on numeric outputs |
| `treehash.py` | one hash for a whole directory |
| `hashing.py`, `paths.py`, `textutil.py` | file hashing, safe relative paths, safe text |
| `environment.py`, `gitinfo.py` | interpreter, installed packages, Git state |
| `runner.py` | runs the command (timeouts, clean environment, output limits) |
| `signing.py` | signing and checking with OpenSSH (`ssh-keygen -Y`) |
| `diffmanifests.py` | `sciproof diff` |
| `ledger.py`, `export.py` | `sciproof list` and the exports (in-toto, OpenLineage) |
| `markdown.py`, `junit.py` | report formats for people and CI tools |
| `suggest.py` | `suggest-inputs`: files a Python command read that are not declared |
| `errors.py` | the exceptions; anything the user can cause is a `SciProofError` (exit status 3) |

## Rules of the house

* **Exit statuses mean something** (`docs/EXIT_CODES.md`): 0 pass, 1 fail, 2 inconclusive, 3 error.
  An unexpected Python traceback must never end with 1.
* **Be honest in messages.** A check says what it checked; "unchanged" is not "correct".
* **Failure cases are tested** as carefully as success cases. See `tests/` for the style: each file
  covers one module; `conftest.py` has helpers for small projects.
* New manifest fields need a schema version decision (`docs/MANIFEST.md`); old manifests must stay readable.

## Running one thing

```bash
python -m pytest tests/test_policy.py -q        # one module's tests
python -m pytest -k "tolerance" -q              # by keyword
python -m sciproof --help                       # the CLI from your checkout
```
