# Numeric comparison: exact rules

`--output-numeric` compares a rerun's output with a copy of the output recorded at capture time.
This page states the rules precisely. Every rule below is covered by a test in
`tests/test_comparison_semantics.py`, `tests/test_numeric.py` or `tests/test_numeric_npy.py`.

A passing comparison means "equivalent under these rules". It does not mean the result is
correct, and it does not mean the same code path was followed (see [Concepts](CONCEPTS.md)).

## The tolerance rule

Two numbers `a` (recorded) and `b` (found) are equivalent when

```
|a - b| <= max(rtol * max(|a|, |b|), atol)
```

* `rtol` is the relative tolerance (default `1e-09`), `atol` the absolute one (default `0`).
  Both are chosen by you at capture time, must be finite and not negative, and are stored in the
  manifest. SciProof cannot tell you which values are scientifically right.
* The rule is symmetric: swapping `a` and `b` never changes the outcome.
* The boundary is inclusive: a difference exactly equal to the tolerance is accepted.
* With `rtol = 0` and `atol = 0` the comparison is exact.
* **A large `rtol` accepts almost anything.** With `rtol >= 1` every number is equivalent to `0`,
  and with `rtol >= 2` a number is equivalent to its negative. `sciproof capture` prints a warning
  when `rtol >= 1`.

## Which values are numbers

| Value | Treated as |
| --- | --- |
| JSON number written with `.` or an exponent (`1.5`, `2e3`) | a float: compared with the tolerance rule |
| JSON integer (`100`) against another JSON integer | **exact**: Python integers are compared exactly, so differences above 2^53 are never hidden and the tolerance is *not* applied |
| JSON integer against a JSON float (`100` vs `101.0`) | numbers: compared with the tolerance rule |
| CSV/TSV cell that `float()` accepts | a float, so `100` vs `101` *is* subject to the tolerance in CSV/TSV |
| CSV/TSV cell containing `_` (`1_000`) | text (Python's `float()` accepts digit separators, SciProof does not) |
| `NaN` | equal to `NaN`, different from every other value |
| `Infinity` / `-Infinity` | equal only to the same infinity |
| strings, `null`, `true`/`false` | must be **exactly** equal and of the same type |

Consequences worth knowing:

* `true` is never equal to `1`, `null` is never equal to `0`, and `"1"` is never equal to `1`,
  whatever the tolerance.
* The integer rule differs between JSON and CSV/TSV on purpose (integers above 2^53 are only
  meaningful in JSON). If you want a count such as `100` vs `101` to tolerate a difference, write
  it as `100.0` in the JSON output.

## Structure

* **JSON objects:** the set of keys must match (a missing or an extra key is a difference);
  key order does not matter.
* **JSON lists:** length and order must match.
* **Duplicate keys** in an object (`{"a": 1, "a": 2}`) are **refused**, not resolved: parsers
  disagree about which value wins, so the content is ambiguous. `capture` rejects such a file and
  a rerun that produces one is reported as `FAIL` ("cannot be compared numerically"). The same
  rule applies to manifests.
* **CSV and TSV:** the number of rows and the number of cells in each row must match. The first row is
  compared like any other row, so a renamed or reordered column is a difference. There is no
  header-aware matching and rows are not sorted.
* **`.npy` arrays** (needs the `numpy` extra): shape and dtype must match, then values are
  compared element-wise with the tolerance rule. Integer and boolean arrays are compared exactly.
  Object and string arrays are refused and pickles are never loaded.

## Where a difference is reported

| Format | Location text | Example |
| --- | --- | --- |
| JSON | `$.key[index]` | `$.metrics.acc[1]: recorded 0.8, found 0.5 (rtol=1e-09, atol=0.0)` |
| CSV / TSV | `rNcM` (row, column; row 0 is the first line), plus the column name from the first line | `r2c1 (column 'loss'): recorded 0.4, found 0.9 (...)` |
| `.npy` | `data[i, j]` | `data[0, 1]: recorded 1.0, found 2.0 (...)` |

At most 10 differences are listed; the rest are summarised. A file that cannot be compared at
all (invalid JSON, unsupported format, non-UTF-8 text, larger than 64 MiB) is reported as `FAIL`
with the reason.

## What is not compared

* The order of CSV rows is never normalised.
* Text is compared byte-for-byte after decoding as UTF-8; there is no Unicode normalisation.
* Units, dtypes of JSON numbers and the formatting of numbers (`1.0` vs `1.00`) are not part of
  the comparison.
