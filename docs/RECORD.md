# Recording without declaring files: `sciproof record`

```bash
sciproof record --name fit -- python fit.py
```

`capture` needs to be told every input and output, and whether an output may be compared exactly
or within a tolerance. That is the first thing that makes a new tool tedious. `record` finds it
out by running the command, and captures with what it found. It also prints the equivalent
`capture` command, so you can commit it or run it again later.

```text
Evidence written to fit.sciproof.json
  inputs found:   2
  outputs, exact: 0
  outputs, numeric: 2
    results/fit.json: varies between runs, tolerance rtol=2e-05
    results/table.csv: varies between runs, tolerance rtol=2e-05
  NOT declared: results/run.log (differs between two identical runs and is not JSON/CSV/TSV)

Equivalent command (commit it, or run it again later):
  sciproof capture --name fit --input data.csv --input fit.py --output-numeric results/fit.json ...
```

## What it does

1. **Run 1**, with Python's audit hook (the one `suggest-inputs` uses). The files the command
   *read* inside the working directory are the inputs; the files it *created or changed* are the
   outputs. Nothing below `.git`, virtual environments, caches or SciProof's own files counts.
2. **Run 2** (skipped by `--fast`), from the same starting state: the files that run 1 created
   are removed first. An output that is the same both times is declared. A `.json`, `.csv` or
   `.tsv` output that differs only in its numbers is declared with a tolerance that covers the
   difference (ten times the spread, as in [`calibrate`](CALIBRATE.md)). **Any other output that
   differs is not declared**, because an exact hash of it could never be verified (a log with a
   timestamp, a file with a random value), and it is listed as `NOT declared` so that nothing is
   silently left out.
3. **Run 3** is the real capture, with those declarations.

So the command runs three times (two with `--fast`, which assumes every output is stable). Plan
for that with a long experiment.

JSON, CSV and TSV outputs are declared as numeric even when both runs were identical, with the
default `rtol=1e-9`, because the integrity check still compares their exact hash and only a rerun
on another machine gets the tolerance. Everything else is compared by hash.

## What it cannot see

* **Only files that Python opened are detected as inputs.** Reads by C extensions through their
  own handles, by other programs, over the network, and environment variables are invisible. For
  a command that is not run by Python (a shell script, `Rscript`, `julia`) no input is detected at
  all and `record` says so: add them with `--input`.
* A file that existed before and was rewritten cannot be restored between runs. It is recorded as
  an output, but its starting state is whatever run 1 left.
* The tolerances come from two runs. Use [`calibrate`](CALIBRATE.md) to measure with more.
* More than 500 inputs or outputs is an error: declare a directory with `capture --input DIR`.

## Options

| Option | Meaning |
| --- | --- |
| `--name NAME` | experiment name, also names the manifest |
| `--input PATH` | add an input by hand (non-Python commands, or a file that was not read) |
| `--exclude PATH` | a file or directory that is neither input nor output; repeatable |
| `--fast` | skip run 2; no tolerance is derived and nothing is excluded for instability |
| `--timeout`, `--max-log-bytes`, `--note`, `--record-hardware`, `--force` | as for `capture` |

The command must exit with status 0 in run 1 and 2, and must create or change at least one file.
`record` executes your own command, like `capture`; it does not ask for confirmation.
