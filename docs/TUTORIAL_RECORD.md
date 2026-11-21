# Tutorial: from a script to CI

This walkthrough uses only Python's standard library and SciProof. Start in an empty directory
with Python 3.11 or newer and install `pip install sciproof==0.7.0`. Commands below were run with
the published SciProof 0.4.1 on macOS / Python 3.13.15. Output blocks are unedited; random spreads
and suggested tolerances will differ in your run.

## 1. Make a small experiment

Save this as `data.csv`:

```csv
x,y
1,3
2,5
3,7
4,9
```

Save this as `fit.py`. It fits a line, adds a little random noise to the slope, and writes a
numeric result plus an intentionally unstable log:

```python
import csv
import json
import random
from pathlib import Path
from statistics import mean

with open("data.csv", newline="", encoding="utf-8") as stream:
    rows = list(csv.DictReader(stream))
xs = [float(row["x"]) for row in rows]
ys = [float(row["y"]) for row in rows]
x_mean, y_mean = mean(xs), mean(ys)
slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / sum(
    (x - x_mean) ** 2 for x in xs
)
intercept = y_mean - slope * x_mean
Path("fit.json").write_text(
    json.dumps({"slope": slope + random.uniform(-0.00001, 0.00001), "intercept": intercept}),
    encoding="utf-8",
)
Path("run.log").write_text(str(random.random()), encoding="utf-8")
```

## 2. Let record discover the files

```bash
sciproof record --name fit -- python fit.py
```

```text
Evidence written to fit.sciproof.json
  inputs found:   2
  outputs, exact: 0
  outputs, numeric: 1
    fit.json: varies between runs, tolerance rtol=5e-05
  NOT declared: run.log (differs between two identical runs and is not JSON/CSV/TSV)

Equivalent command (commit it, or run it again later):
  sciproof capture --name fit --input data.csv --input fit.py --output-numeric fit.json --tolerance fit.json rtol=5e-05 -- python fit.py
Check the list above: only files that Python opened are detected as inputs.
The tolerances come from two runs; 'sciproof calibrate' measures them with more.
```

The manifest is the evidence file. The two inputs are `data.csv` and `fit.py`; zero outputs are
compared exactly, and `fit.json` gets a numeric tolerance from the observed spread. `run.log`
changes between runs and is not numeric JSON/CSV/TSV, so its contents are **not verified**.
The equivalent command makes these declarations explicit. `record` runs your program three
times: two discovery/stability runs and the capture itself. See [record](RECORD.md).

## 3. Verify, then change an input

```bash
sciproof verify fit.sciproof.json
```

```text
SciProof verification of 'fit'

  [PASS] input data.csv: unchanged (SHA-256 matches)
  [PASS] input fit.py: unchanged (SHA-256 matches)
  [PASS] output fit.json: unchanged (SHA-256 matches)

  note: scope: artifact integrity only. The recorded command was NOT executed, so this does not show that the results can be reproduced.

  basis: PASS because all 3 performed checks succeeded; this covers only the checks listed above, not what is under 'not checked'
  not checked: reproducibility: the recorded command was not executed
  not checked: scientific validity: a matching result is not shown to be correct
  not checked: files that were not declared as inputs or outputs
  not checked: authenticity: manifests are unsigned, so who produced this evidence is not verified

RESULT: PASS
```

To execute your recorded command again, run `sciproof verify fit.sciproof.json --rerun --yes`.
This run passed all five checks, including numeric equivalence at `rtol=5e-05`. Only use
`--yes` for commands you trust; omit it for an interactive confirmation. Rerun outputs are
moved aside and the baseline is restored.

Now edit the first data row from `1,3` to `1,4`, leaving the manifest alone, and run:

```bash
sciproof verify fit.sciproof.json
```

```text
SciProof verification of 'fit'

  [FAIL] input data.csv: modified since capture (SHA-256 differs)
  [PASS] input fit.py: unchanged (SHA-256 matches)
  [PASS] output fit.json: unchanged (SHA-256 matches)

  note: scope: artifact integrity only. The recorded command was NOT executed, so this does not show that the results can be reproduced.

  basis: FAIL because 1 (input data.csv) check(s) contradict the evidence; one failed check is enough
  not checked: reproducibility: the recorded command was not executed
  not checked: scientific validity: a matching result is not shown to be correct
  not checked: files that were not declared as inputs or outputs
  not checked: authenticity: manifests are unsigned, so who produced this evidence is not verified

RESULT: FAIL
```

That command exits 1. Restore the first row to `1,3` before continuing.

## 4. Measure noise and capture with the suggestion

To demonstrate a tolerance that is too tight, replace the evidence with an explicit capture
using the default `rtol=1e-9`. `--force` replaces the existing manifest; it is intentional here.

```bash
sciproof capture --name fit --force --input data.csv --input fit.py --output-numeric fit.json -- python fit.py
```

```text
Evidence written to fit.sciproof.json
  command exit code: 0
  inputs recorded:   2
  outputs recorded:  1
Note: stdout/stderr are stored in the manifest; check them for secrets before sharing it.
```

```bash
sciproof calibrate fit.sciproof.json --runs 10 --yes
```

```text
SciProof calibration of 'fit' (10 runs of the recorded command, plus the recorded baseline)

  [DIFFERS] fit.json (numeric): varies by up to 8.21e-06 relative (1.64e-05 absolute); 1 value(s) outside the recorded tolerance (rtol=1e-09, atol=0.0)
           suggestion: --tolerance fit.json 'rtol=9e-05'

  note: 10 runs only sample the variation: the suggested rtol is 10 times the largest relative spread seen, and a longer or different run can vary more.
  note: this measures noise between runs on this machine, not whether the result is right; the manifest and the outputs were not changed.

RESULT: VARIES
```

`VARIES` exits 1 and leaves your baseline intact. Use **your** suggested tolerance in a new
capture. These commands used the `9e-05` suggestion above; the capture printed the same five
lines as the preceding capture, and the rerun's exit status was 0:

```bash
sciproof capture --name fit --force --input data.csv --input fit.py --output-numeric fit.json --tolerance fit.json rtol=9e-05 -- python fit.py
sciproof verify fit.sciproof.json --rerun --yes
```

Ten runs sample noise on one machine. They do not establish an appropriate scientific
tolerance or guarantee another machine will agree. See [calibrate](CALIBRATE.md).

## 5. Check the evidence in CI

Commit `fit.py`, `data.csv`, `fit.json`, `fit.sciproof.json` **and** `fit.sciproof.d/` (the numeric
baseline), preserving their relative paths. Leave `run.log` and rerun scratch directories out.
Save this as `.github/workflows/evidence.yml` in that repository:

```yaml
name: evidence
on: [pull_request]
permissions:
  contents: read
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: hayagrivai-labs/sciproof@v0.7.0
        with:
          manifest: fit.sciproof.json
          version: "0.6.0"
```

The action verifies integrity without executing the manifest's command. A changed declared
file fails the job. The repository tests exercise this action; this walkthrough did not deploy
a new hosted workflow. Pin action commits if your policy requires it. See [CI](CI.md) for
reports, policies and the precautions required for trusted reruns.

## What this does not see

Only files Python opened are detected as inputs. Reads through a C extension's own handles,
child programs, network calls and environment variables can be missed. Declare missing inputs
with `--input`, inspect the discovered list, and remember that `run.log` was deliberately left
out. A `PASS` covers the listed checks, not undeclared files or scientific correctness.
