# SciProof for Researchers & Paper Authors

Computational reproducibility is now an explicit requirement for top journals (*Nature*, *Science*) and major conferences (NeurIPS, ICML, ICLR, ACM, IEEE). Yet researchers spend days copying numbers into LaTeX tables and worrying if their results still run months later when reviewers ask.

SciProof was designed specifically to solve this workflow with **zero code modifications**, local-first reproducibility manifests, and direct paper integration.

---

## 1. Zero-Friction Experiment Recording

You do **not** need to modify your Python code or add decorators. Run your existing experiment with `record`:

```bash
sciproof record --name main-experiment -- python train.py --epochs 50
```

SciProof automatically:
* Intercepts and records which input datasets were read.
* Hashes all generated figures, tables, and metric files with SHA-256.
* Captures the execution environment (Python version, OS, package versions, git commit).
* Writes a verifiable evidence manifest: `main-experiment.sciproof.json`.

---

## 2. Eliminate Paper Number Drift (Direct Typst & LaTeX Export)

The most common source of error in scientific papers is **copy-paste drift**: an experiment is rerun, but the author forgets to update a cell in Table 2 of the manuscript draft.

### Typst Export
Typst is the modern scientific typesetting standard. Export verified metrics as native Typst variables:

```bash
sciproof export main-experiment.sciproof.json --format typst -o evidence.typ \
  --value accuracy out/metrics.json "$.test_accuracy" \
  --value fone out/metrics.json "$.f1_score"
```

This generates `evidence.typ`:
```typst
// SciProof 0.6.0: values of main-experiment.sciproof.json (integrity verification: PASS)
#let accuracy = 0.942
#let fone = 0.918
```

In your paper's `.typ` document:
```typst
#import "evidence.typ": accuracy, fone

Our proposed architecture achieved a test accuracy of #accuracy and an F1-score of #fone.
```

### LaTeX Export
For LaTeX documents:

```bash
sciproof export main-experiment.sciproof.json --format latex -o paper_values.tex \
  --value accuracy out/metrics.json "$.test_accuracy" \
  --value fone out/metrics.json "$.f1_score"
```

This generates `paper_values.tex`:
```latex
% SciProof 0.6.0: values of main-experiment.sciproof.json (integrity verification: PASS)
\newcommand{\accuracy}{0.942}
\newcommand{\fone}{0.918}
```

In your paper's `.tex` document:
```latex
\input{paper_values.tex}
...
Our proposed architecture achieved a test accuracy of \accuracy{} and an F1-score of \fone{}.
```
If the underlying experiment output changes or is modified, your paper's numbers update automatically, or `sciproof verify` alerts you before submission.

---

## 3. Calibrate Tolerances for Floating-Point Noise

Floating-point operations across CPU architectures or GPU drivers often introduce minor differences ($10^{-6}$). Instead of guessing tolerances:

```bash
sciproof calibrate main-experiment.sciproof.json --runs 5
```

SciProof runs your experiment repeatedly, measures the variance across outputs, and suggests the exact `--tolerance` (`rtol`/`atol`) needed so reviewers get a deterministic `PASS`.

---

## 4. One-Click Reproducibility Badge for Paper Repositories

Add a verified reproducibility badge to your GitHub repository or project page:

```markdown
[![Reproducibility Evidence: SciProof](https://img.shields.io/badge/reproducibility-SciProof-2ea44f)](https://hayagrivai-labs.github.io/sciproof/)
```

Or generate a dynamic static SVG from CI verification:

```bash
sciproof badge main-experiment.sciproof.json -o docs/evidence.svg
```

---

## 5. Artifact Evaluation & Reviewer Checklist

When submitting code to conference **Artifact Evaluation** tracks (ACM, IEEE, NeurIPS, USENIX):

> **Ready-to-use template:** See [Artifact Evaluation Submission Template](ARTIFACT_EVALUATION_TEMPLATE.md) for a complete, copy-pasteable appendix for your paper's code repository.

1. Commit your `*.sciproof.json` manifest along with the code.
2. In your `README.md` or artifact submission form, instruct reviewers to verify with:
   ```bash
   pip install sciproof
   sciproof verify main-experiment.sciproof.json --rerun
   ```
3. The reviewer gets an unambiguous, verifiable verdict:
   ```text
   SciProof: PASS
   all files match recorded hashes; outputs match within declared tolerances.
   ```

---

## 6. How to Cite SciProof

If you use SciProof in a published paper or artifact submission, cite it using:

```bibtex
@software{sciproof,
  author = {Pandey, Alok and {HayagrivAI Labs}},
  note = {ORCID: 0009-0007-7867-5939},
  title = {SciProof: Verifiable Computational Evidence and Reproducibility for Scientific Experiments},
  url = {https://hayagrivai-labs.github.io/sciproof/},
  doi = {10.5281/zenodo.23676138},
  version = {0.7.0},
  year = {2026}
}
```
Or use the repository's [CITATION.cff](../CITATION.cff) file.
