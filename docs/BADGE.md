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
