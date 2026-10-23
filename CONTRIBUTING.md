# Contributing

Thanks for helping. SciProof is small on purpose; the most valuable contributions right now
are **real-world feedback** (does it fit how you run experiments?), bug reports with a minimal
manifest, and tests.

## Where to start

Every task below is a real, open issue with the problem, the files to touch and the acceptance
criteria written down. Pick one, comment `/take` on it (it is then assigned to you; an assigned
issue with no activity for 21 days goes back to the pool), and ask questions in the issue. You can
hold two at a time.

* **First contribution (a few hours):** [good first issue](https://github.com/hayagrivai-labs/sciproof/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
* **Some knowledge of the code:** [difficulty: medium](https://github.com/hayagrivai-labs/sciproof/issues?q=is%3Aissue+is%3Aopen+label%3A%22difficulty%3A+medium%22)
* **Design work (discuss the approach first):** [needs design](https://github.com/hayagrivai-labs/sciproof/issues?q=is%3Aissue+is%3Aopen+label%3A%22needs+design%22)
* **A maintainer will help you through it:** [mentor available](https://github.com/hayagrivai-labs/sciproof/issues?q=is%3Aissue+is%3Aopen+label%3A%22mentor+available%22)
* **Tests, docs and tutorials count as much as code:** [testing](https://github.com/hayagrivai-labs/sciproof/issues?q=is%3Aissue+is%3Aopen+label%3Atesting),
  [area: docs](https://github.com/hayagrivai-labs/sciproof/issues?q=is%3Aissue+is%3Aopen+label%3A%22area%3A+docs%22)

Nothing fits? Found a bug, or you use SciProof in a way it does not support yet? Open an issue or a
[discussion](https://github.com/hayagrivai-labs/sciproof/discussions). A well-described problem from real use is a contribution.

How the code is organised is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### What happens to your pull request

A maintainer reads every pull request. We check that the change is small and focused, that it has
a test (including the failure case), that the checks pass, and that messages and docs do not claim
more than the code verifies. Review comments are about the change, not the person; it is normal for
a first pull request to go through a round or two of changes. If a pull request stalls, say so in
a comment and someone will pick it up.

## Development setup

No setup on your machine: [open the repository in GitHub Codespaces](https://codespaces.new/hayagrivai-labs/sciproof)
(or in any dev container). It installs Python 3.12 and the development dependencies, so `ruff` and
`pytest` work right away.

On your own machine:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Checks (run these before opening a pull request)

```bash
ruff check .
ruff format --check .
python -m pytest
python examples/demo.py
```

CI runs the same on Python 3.11, 3.12 and 3.13 on Linux, Windows and macOS.

## Guidelines

* Python 3.11+, standard library only at runtime. A new dependency needs a strong reason.
* No network access, cloud accounts or telemetry. Everything must work offline.
* Treat manifests as untrusted input. Any new code path that reads a path or string from a
  manifest must validate it (see `paths.py`, `manifest.py`, `textutil.py`) and have a test.
  Never execute anything from `inspect` or plain `verify`.
* Be honest in messages and docs: do not claim more than a check shows (see
  `docs/CONCEPTS.md`).
* Add tests for behaviour changes, including failure cases. Do not weaken a test to make it pass.
* Keep pull requests focused. Explain *why*, not only *what*.

## Licence

By contributing you agree that your contribution is licensed under the MIT licence of this
project. MIT was chosen because it is short, permissive and well understood by universities and
companies, so researchers can use and embed the tool without legal review. If patent grants
become important, relicensing to Apache-2.0 can be discussed with all contributors.

## Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Security issues: see [SECURITY.md](SECURITY.md).
