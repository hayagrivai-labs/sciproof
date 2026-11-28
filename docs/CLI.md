# Command Line Interface (CLI)

`sciproof` provides commands to record, inspect, verify, calibrate, and compare computational
experiments.

## Global options

* `--version`: Print the SciProof version and exit.
* `-q`, `--quiet`: Suppress decorative banners, progress notes, and headers. Only emit the final
  status summary on `stdout`, routing diagnostics and warnings to `stderr` if a failure occurs.

## Commands

### `verify`

Check that an evidence manifest matches current outputs:

```bash
sciproof verify baseline.sciproof.json
```

* `--format {text,json,markdown,junit,sarif,html}`: Output format for the verification report (default: `text`).
  * `text`: Human-readable summary and detailed checks.
  * `json`: Machine-readable JSON report adhering to `sciproof.report` v1.0.
  * `markdown`: GitHub-flavored Markdown suitable for `$GITHUB_STEP_SUMMARY`.
  * `junit`: JUnit XML format for CI test runners.
  * `sarif`: SARIF 2.1.0 format with rule IDs `SP001`-`SP003` for GitHub Code Scanning (see [docs/REPORT.md](REPORT.md#sarif-210-report)).
  * `html`: Standalone, interactive HTML report with visual tolerance meters (see [docs/REPORT.md](REPORT.md#interactive-html-report)).

With `--quiet` / `-q`, header banners and check lists are omitted, producing minimal output on
`stdout`:

```bash
sciproof verify baseline.sciproof.json -q
# Output:
# RESULT: PASS
```

If verification fails or is inconclusive, failure diagnostics and reasons are written to `stderr`:

```bash
sciproof verify modified.sciproof.json -q
# stdout: RESULT: FAIL
# stderr: [FAIL] output out/result.json: modified since capture (SHA-256 differs)
```

### `record`

Automatically detect read inputs and produced outputs without declaring them:

```bash
sciproof record --name myrun -- python train.py
```

With `-q` / `--quiet`, verbose command recreation hints and declarations are suppressed:

```bash
sciproof record --name myrun -q -- python train.py
# Output:
# Evidence written to myrun.sciproof.json
```

### `calibrate`

Measure output variance over multiple re-runs to suggest tolerances:

```bash
sciproof calibrate baseline.sciproof.json --runs 5 --yes
```

With `-q` / `--quiet`:

```bash
sciproof calibrate baseline.sciproof.json --runs 5 --yes -q
# Output:
# RESULT: STABLE
```

### `pipeline`

Verify end-to-end multi-step workflow pipelines declared in a TOML file:

```bash
sciproof pipeline verify pipeline.toml
```

Verifies the integrity of every individual step's manifest and enforces cross-step cryptographic chain-of-custody (verifying that intermediate output SHA-256 hashes match downstream input SHA-256 hashes).

### `export`

Export a manifest to other metadata specifications or typesetting variables:

```bash
sciproof export baseline.sciproof.json --format in-toto        # in-toto v1 Statement
sciproof export baseline.sciproof.json --format dsse           # in-toto wrapped in DSSE envelope
sciproof export baseline.sciproof.json --format ro-crate       # RO-Crate 1.1 Process Run Crate JSON-LD
sciproof export baseline.sciproof.json --format openlineage    # OpenLineage run events
sciproof export baseline.sciproof.json --format typst -o val.typ  # Typst #let variables
sciproof export baseline.sciproof.json --format latex -o num.tex  # LaTeX \newcommand macros
```


You can also profile concurrency drift across core counts:

```bash
sciproof calibrate baseline.sciproof.json --threads 1,2,4,8 --yes
```



### `capture`

Run an experiment command and record an evidence manifest:

```bash
sciproof capture --name baseline \
  --input data/input.csv \
  --output-numeric out/metrics.json --rtol 1e-4 \
  --output out/report.txt \
  -- python experiment.py
```

### Glob patterns for inputs and outputs

`capture` supports wildcard glob patterns for `--input`, `--output`, and `--output-numeric`:

```bash
sciproof capture --name experiment \
  --input "data/**/*.csv" \
  --output "out/tables/*.tsv" \
  --output-numeric "out/models/*.npz" \
  -- python train.py
```

* **Expansion at capture time**: Glob patterns are evaluated and expanded when `capture` runs. Matching files are recorded individually in the manifest. The manifest stores exact relative file paths, not unexpanded patterns.
* **Safety rules**: Patterns must be relative to the workspace directory. Absolute patterns (starting with `/`) and directory traversals (`..`) are rejected with `UnsafePathError`.
* **Regular files only**: Recursive globs (like `**/*`) match regular files only; directories matched by the pattern are dropped and not hashed as files. Directory symlinks are not traversed.
* **Strict error on empty match**: If an input glob (`--input`) matches no files, `capture` aborts immediately with an error and writes no evidence. If an output glob (`--output` or `--output-numeric`) matches no files after execution, `capture` aborts with an error (exit code `3`) rather than creating a manifest claiming nonexistent outputs.

### `diff`

Compare two evidence manifests:

```bash
sciproof diff baseline.sciproof.json updated.sciproof.json
```

With `-q` / `--quiet`:

```bash
sciproof diff baseline.sciproof.json updated.sciproof.json -q
# Output:
# RESULT: IDENTICAL
```

If differences are found, `RESULT: DIFFERENT` is output on `stdout` (exit code `1`) and a summary is
printed to `stderr`.

## Shell completion

Bash also has a standalone script, `examples/completions/sciproof.bash`, that needs no extra
package: run `source examples/completions/sciproof.bash`, or add that line (with the full path) to
`~/.bashrc`. It completes subcommands, options and the values of `--format`; paths use Bash's normal
file-name completion.

SciProof supports tab completion for subcommands and long options via `argcomplete`:

```bash
python -m pip install "sciproof[completion]"
eval "$(register-python-argcomplete sciproof)"
```

### PowerShell

PowerShell (Windows PowerShell 5.1 and PowerShell 7+) has its own completion script that needs no
extra package. It completes subcommands, options and the values of `--format`; paths fall back to
the normal file-name completion. Load it in the current session, or add the same line to `$PROFILE`:

```powershell
. .\examples\completions\sciproof.ps1
```

Windows PowerShell 5.1 does not call completers for a bare `-` or `--`, so type the first letter
of the option (for example `--f`, then Tab).

### Fish

Fish has a standalone script that needs no optional Python package. Load it in the current
session:

```fish
source examples/completions/sciproof.fish
```

To load it automatically, copy `sciproof.fish` into `~/.config/fish/completions/` (or your Fish
configuration directory's `completions/` folder). It completes the current subcommands, their
options, fixed choices such as `--format`, and file/directory arguments. After `capture`,
`record`, or `suggest-inputs` options, it delegates completion of the recorded command to Fish,
including after `--`; completion does not run the experiment or write evidence.
