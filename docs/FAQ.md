# FAQ

Every answer restates something documented elsewhere; follow the links for the full context.

## Does `PASS` mean my result is correct?

No. A `PASS` is a statement about integrity, execution reproducibility and numerical
equivalence within the checks listed in the report. Scientific validity is not addressed: a
deterministic program that implements a wrong model reproduces its wrong answer perfectly.
See [Concepts](CONCEPTS.md).

## Does `sciproof inspect` run my command?

No. `inspect` and plain `verify` never execute the recorded command. Only
`verify --rerun` does, after an interactive confirmation or `--yes`. See
[Security](../SECURITY.md).

## Is `--rerun` sandboxed?

No. A confirmed rerun is arbitrary code execution with your permissions. Run untrusted
manifests in a disposable VM or container. See [Security](../SECURITY.md).

## What is the difference between `FAIL` and `INCONCLUSIVE`?

`FAIL` means something was found to differ from the evidence. `INCONCLUSIVE` means a check
could not be carried out (for example an input file is missing, or the rerun timed out), so
nothing can be said. `INCONCLUSIVE` is never treated as success. See
[Exit codes](EXIT_CODES.md).

## Does it need an account, a server or internet access?

No. The project guidelines require that everything works offline, with no cloud accounts and no
telemetry, and the package has no runtime dependencies (see [Contributing](../CONTRIBUTING.md)).

## Which tolerance should I choose?

SciProof cannot tell you. The tolerance (`--rtol`, `--atol`) is chosen by you at capture time and
stored in the manifest; the [NumPy & pandas tutorial](TUTORIAL_NUMPY_PANDAS.md) discusses how to
pick one.

## Which files can be compared with a tolerance?

`.json`, `.csv` and `.tsv` (standard library only) and `.npy` arrays when NumPy is installed
(`pip install 'sciproof[numpy]'`). Everything else is compared by exact SHA-256.

## Will I get bit-identical results on another machine?

Not promised. Bitwise reproducibility across different hardware or platforms is not guaranteed;
see the limits listed in [Concepts](CONCEPTS.md).

## Are manifests signed?

No. A tested recipe using `ssh-keygen -Y` is described in [Signing manifests](DESIGN_SIGNING.md).

## How is it different from DVC or MLflow?

See the [comparison](COMPETITIVE_ANALYSIS.md). In short: if you already use DVC you largely have
integrity checking; SciProof's focus is the verification step with an explicit verdict and
declared tolerances.

## Which operating systems are tested?

Linux, Windows and macOS, with Python 3.11 to 3.13, in the project's CI.

## How do I install it?

See the install section of [Getting started](../README.md), which always shows the currently
supported method.
