# Updating a baseline

An intended change (a new dataset, a better model) makes the recorded evidence out of date.
`sciproof update` records it again **with exactly the declarations the manifest already has**, then
shows you what changed, so refreshing a baseline is one command and a reviewable diff instead of
retyping a long `capture` line.

```bash
sciproof update baseline.sciproof.json --dry-run   # show what would happen; run nothing
sciproof update baseline.sciproof.json             # asks for confirmation
sciproof update baseline.sciproof.json --yes --note "after the new tokenizer"
```

```
Updated baseline.sciproof.json (command exit code 0)

SciProof diff: 'baseline' -> 'baseline'

  inputs (1)
    ~ params.json: content changed (sha256 134bd6e1df02 -> 9e7979e1663c, 13 -> 13 bytes)
  outputs (1)
    ~ metrics.json: content changed (...); outside the baseline tolerance (rtol=1e-06, atol=0.0): ...
```

## What is repeated

The command, the declared inputs and outputs (files and directories), the numeric tolerance, the
claims (written back in the syntax of `--claim`), the note (unless `--note` replaces it) and the
hardware record (if there was one). The manifest is replaced, together with its `NAME.sciproof.d/`
snapshots. The diff compares the **old** manifest, kept aside before the capture, with the new one;
numeric outputs are judged with the old tolerance, as in [`sciproof diff`](DIFF.md).

A manifest whose numeric outputs have different tolerances cannot be updated (`capture` takes one
tolerance for all of them); capture it again by hand. A manifest that is not named
`<experiment>.sciproof.json` is refused, because the new one would be written under another name.

## Safety

* **It executes the recorded command**, like `verify --rerun`, so it needs the same confirmation
  (interactive, or `--yes`) and shows the command first. Only update manifests you trust.
* The declared outputs are **removed before the command runs** and restored if the capture fails.
  Otherwise a command that never wrote its outputs would turn the old files into the new baseline.
  If the command exits non-zero the failure is recorded, as in `capture` (exit status 1).
* If a `.sig` file sits next to the manifest it signs the **old** manifest. `update` says so and
  leaves the file for you to replace with [`sciproof sign`](SIGNING.md).
* Claims that no longer hold for the new result are still recorded, with a warning, like at capture.

## Python

```python
result = sciproof.update_baseline("baseline.sciproof.json", assume_yes=True)
print(result.diff.summary)
```
