# Show that your results come with evidence

If you record an experiment with SciProof, you can say so in your README, paper repository or
data release. Readers then know where to look and how to check it.

```markdown
[![Evidence: SciProof](https://img.shields.io/badge/evidence-SciProof-2ea44f)](https://hayagrivai-labs.github.io/sciproof/)
```

The badge is a promise about **your repository**, not a result: keep it honest.

* Say what the evidence covers, for example "results in `results/` have manifests; check with
  `sciproof list --check results/`".
* Do not add it unless the manifests are committed and `sciproof list --check` passes. A CI job
  using the [GitHub Action](CI.md) keeps that true.
* For a stronger statement, sign the manifests ([Signing](SIGNING.md)) and say whose key to trust.

A badge that links to a `verify` run in CI is better than one that only claims; see
[CI](CI.md).

## A badge that shows the last verification: `sciproof badge`

```bash
sciproof badge baseline.sciproof.json -o docs/evidence.svg          # integrity only
sciproof badge baseline.sciproof.json --rerun --yes --clean-env -o docs/evidence.svg
```

writes a small static SVG, for example `sciproof | PASS · integrity · 2026-10-23`, green for
`PASS`, red for `FAIL` and amber for `INCONCLUSIVE`. It is drawn by the program itself: no service,
no network, and nothing from the manifest is put into it, only the verdict, whether the command was
executed again (`rerun`) or only the files were checked (`integrity`), and the UTC date.

* The exit status is the verdict's, like `verify` (0, 1 or 2), and the badge is written whatever the
  verdict is, so a failing check shows up as a red badge instead of an old green one.
* Write it in the CI job that verifies and commit or publish the result there. A badge written by
  hand, or one that is never refreshed, claims something nobody checked.
* `integrity` and `rerun` are different statements; the badge says which one it is. Neither says that
  the result is correct.
* `--rerun` executes the recorded command, with the same confirmation (`--yes` in CI) and the same
  care as `verify --rerun`.
