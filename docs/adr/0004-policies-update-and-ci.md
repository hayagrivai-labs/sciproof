# ADR 0004: Policies, `update`, and the CI integrations

* Status: accepted, 2026-10-21
* Related: [ADR 0001](0001-explainable-verification.md), [ADR 0003](0003-claims-signing-and-clean-reruns.md),
  [Policies](../POLICY.md), [Updating a baseline](../UPDATE.md), [CI](../CI.md)

## Context

After 0.2.0 SciProof could record, verify, compare and sign evidence, but each team still had to say
"what counts as acceptable evidence" in shell scripts around it, refreshing a baseline meant retyping
a long `capture` command, and using it in CI meant gluing commands together by hand.

## Decisions

### 1. A policy is a file of rules about the evidence, not a second configuration of the tool

TOML (`tomllib` is in the standard library from Python 3.11, so there is no new dependency), a closed
set of rules in three sections, every rule a check of the report. The file comes from the command
line, never from the manifest, so evidence cannot weaken its own policy.

* **Strict:** unknown sections or rules, wrong types and out-of-range numbers are errors that name
  the rule. A policy that is silently misread is worse than none. It is loaded **before** a rerun, so
  a typo cannot cost a re-execution.
* **Missing evidence is `INCONCLUSIVE`:** a rule that needs a signature, verified without
  `--allowed-signers`, cannot say anything and must not read as a pass.
* **Rejected:** an expression language for rules (it grows into a program with its own security
  review); policy files that include or extend each other (hidden rules); rules that read the network
  or the clock other than for the age of the evidence.

### 2. `update` is a capture with the manifest's own declarations

The manifest already holds everything `capture` was given, so `update` reconstructs the call instead of
inventing a second description of the experiment. It takes the same confirmation as a rerun, because it
runs the same command, and it removes the declared outputs first (restoring them on failure) for the
reason in ADR 0003: a stale output must not become the baseline. The result is a diff against the old
manifest, so a refreshed baseline arrives as a reviewable change.

* **Rejected:** keeping the old manifest as `.previous` next to the new one (clutter; Git already
  keeps history); updating in place without a diff (the point is to see what moved); silently
  re-signing (the signature is the user's decision).

### 3. JUnit is another view of the same report, and the Action is thin

JUnit maps `FAIL` to a failure and `INCONCLUSIVE` to a skipped test **with its reason**, because the
check could not be performed; the exit status stays 2. The Action is a composite action that only
builds the command line from its inputs (through environment variables, never string interpolation)
and writes the Markdown report to the job summary; all behaviour stays in the CLI, which is what the
repository's CI tests, including the action itself.

* **Rejected:** a JavaScript action (a second implementation to keep in step with the CLI); running
  the verification twice to get both a Markdown summary and JSON (a rerun would execute the command
  twice) - `--save-json` writes the JSON from the same run.

### 4. Faster package listing: read the directory names, keep the exact way as the fallback

Parsing every METADATA file only to learn a package's name dominated a capture in large environments.
The `dist-info` directory name already carries name and version. It is used when it matches the
standard layout and METADATA only when it does not. Package names are compared in their PEP 503 normal
form so that both spellings of the same package compare equal, which also keeps manifests recorded by
0.2.x comparable. Running the other probes concurrently was measured and gave nothing, so it was not
kept.

## Consequences

* Policies and the Action add no runtime dependency.
* A new rule needs a documented meaning, a test for pass, fail and missing evidence, and an entry in
  `docs/POLICY.md`.
* The recorded package *name* of a dotted package now comes from the directory name (`jaraco_context`
  instead of `jaraco.context`); comparisons ignore the difference.
