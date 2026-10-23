# Choosing a tolerance: `sciproof calibrate`

```bash
sciproof calibrate baseline.sciproof.json --runs 5
```

A tolerance (`--rtol`, `--atol`) is a guess until someone has looked at the noise. `calibrate`
runs the recorded command several times and measures it.

It **executes the recorded command**, `--runs` times (2 to 50, default 5), exactly like
`verify --rerun`, so it asks for the same confirmation (or needs `--yes`) and the same care
applies: only calibrate manifests you trust. The declared outputs are moved aside before the first
run and put back afterwards, and the manifest is not changed.

## What it reports

For every declared output, the runs are compared with each other and with the recorded baseline:

| Output | Result |
| --- | --- |
| compared **exactly** | `OK` if every run has the same content (and the recorded one), otherwise `DIFFERS`: the output is not deterministic, so an exact hash can never be verified. Declare it with `--output-numeric` if it is a table of numbers, otherwise remove the source of the variation (a timestamp, a random seed, an unordered set). |
| **numeric** | the largest spread of any number, absolute and relative, and whether every value is inside the tolerance recorded in the manifest. If not, a suggested `--tolerance` line. |

Text, booleans, missing keys and a changed exit code are reported as differences, never as noise.

```text
  [DIFFERS] out/m.json (numeric): varies by up to 8.8e-05 relative (2.2e-05 absolute);
            1 value(s) outside the recorded tolerance (rtol=1e-09, atol=0.0)
           suggestion: --tolerance out/m.json 'rtol=9e-04'
```

The result is `STABLE` (exit status 0) or `VARIES` (1); 2 means the confirmation was declined and
3 an error ([exit codes](EXIT_CODES.md)).

## How to read the suggestion

The suggested `rtol` is **ten times the largest relative spread seen**, rounded up to one digit.
A handful of runs only samples the noise: a longer run, another machine or another day can vary
more. Treat the suggestion as a starting point, capture again with it (`--tolerance`, see
[Numeric comparison](COMPARISON.md)), and run `calibrate` once more on the machine where the
verification will happen.

`calibrate` measures noise between runs on this machine. It does not show that a tolerance is
scientifically appropriate or that the result is correct.
