# Claims

A **claim** is a statement about one value in an output file that SciProof can check
mechanically, for example "the accuracy is at least 0.9". It is recorded in the manifest when you
capture, and `verify` checks it every time, on the recorded output and, with `--rerun`, on the
output of the rerun.

```bash
sciproof capture --name baseline \
  --input params.json --input train.py --output-numeric metrics.json \
  --claim metrics.json '$.accuracy >= 0.9' \
  --claim metrics.json '$.loss ~= 0.25 atol=0.01' \
  -- python train.py
sciproof verify baseline.sciproof.json
```

```
  [PASS] claim metrics.json: $.accuracy >= 0.9: holds: $.accuracy is 0.92, claim is >= 0.9
```

## Syntax

`--claim OUTPUT 'LOCATION OP VALUE'`

* `OUTPUT` is a `.json`, `.csv` or `.tsv` file that you also declared with `--output` or
  `--output-numeric`.
* `LOCATION` is the one that appears in difference reports: `$.key[0].name` for JSON,
  `r3c1` (row, column; row 0 is the first line) for CSV and TSV. Keys that contain dots, spaces or brackets
  cannot be addressed.
* `OP` is one of `==`, `!=`, `<`, `<=`, `>`, `>=`, `~=`.
* `VALUE` is a JSON number, a string in double quotes, `true`, `false` or `null`. The ordering
  operators and `~=` need a number.
* `~=` means "approximately": `|found - value| <= max(rtol * max(|found|, |value|), atol)`, with
  `rtol=` and `atol=` after the value (both default to 0, which is exact).

## Rules

* `==` and `!=` treat a number as a number (`1` equals `1.0`), and compare booleans, strings and
  `null` exactly and by type: `true` is not `1`.
* A value that is not a number never satisfies `<`, `<=`, `>`, `>=` or `~=`; `NaN` satisfies none of
  `==`, `<`, `<=`, `>`, `>=`, `~=` (and `!= x` holds).
* A location that is not in the file means the claim does not hold.

## What `verify` does with them

| Situation | Result |
| --- | --- |
| The output still has its recorded hash and the claim holds | `PASS` |
| ... and the claim does not hold | `FAIL` |
| The output no longer matches the recorded hash, or is missing | `INCONCLUSIVE` (the output check itself already `FAIL`s): a claim about a modified file says nothing about the evidence |
| With `--rerun`: the rerun's output satisfies the claim / does not | `PASS` / `FAIL`, as a separate check called `rerun claim ...` |
| With `--rerun`: the rerun did not produce the file | `INCONCLUSIVE` |

If a claim does not hold when you capture, SciProof still records it and prints a warning;
verification then keeps reporting it. The JSON evidence of each check has the `output`, `location`,
`op`, `value` and the `found` value.

## What a claim does not show

A claim that holds shows only that **this value satisfies this comparison**. It does not show that
the comparison is the right criterion, that the number means what its name suggests, or that the
experiment is sound. Every report that checks claims says so under "not checked". SciProof does not
extract or interpret claims from papers.

Claims are part of the manifest (schema 1.1), so [`sciproof diff`](DIFF.md) lists claims that were
added or removed between two runs, and [`sign`](SIGNING.md) covers them.
