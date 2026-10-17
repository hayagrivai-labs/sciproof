# Design note: exporting a manifest as an RO-Crate (Process Run Crate)

Status: **research note, no code.** Discussion: issue #7.

## Why look at this

[RO-Crate](https://www.researchobject.org/ro-crate/) is a community format for describing
research outputs, and the *Workflow Run RO-Crate* family of profiles describes how a computation
was executed. A SciProof manifest holds much of the same information, so an export could let
SciProof evidence travel into tools that understand RO-Crate. RO-Crate **describes**; it does not
verify anything, which is exactly the gap SciProof fills. The two are complementary, not competing.

Sources: the [Process Run Crate profile, version 0.6](https://www.researchobject.org/workflow-run-crate/profiles/process_run_crate)
(extends RO-Crate 1.3; permalink `https://w3id.org/ro/wfrun/process/0.6`) and its
[changelog](https://www.researchobject.org/workflow-run-crate/profiles/changelog): 0.6 is dated
2026-09-07 and, per the changelog, is not backward compatible with RO-Crate 1.1 / Workflow
RO-Crate 1.0 because some IRI mappings changed.

## Mapping

"Not defined" means the profile page does not define the property; the RO-Crate core
specification and other vocabularies were **not** checked for alternatives.

| SciProof manifest | Process Run Crate (0.6) | Notes |
| --- | --- | --- |
| experiment (`name`) | `CreateAction.name` (SHOULD) | |
| `command.argv` | `CreateAction.description` (SHOULD, informational only) | The profile has no structured command line; free text only. |
| Python interpreter and packages | `instrument` (MUST) pointing at a `SoftwareApplication` with `name`/`version` (SHOULD); `softwareRequirements` (MAY) for packages | The tool executed would be the command itself; the interpreter is only meaningful for Python commands. |
| `inputs[]` (files) | `object` (MAY), entities typed `File` | |
| `inputs[]` / `outputs[]` (directories, schema 1.1) | `Dataset` (the profile names `Dataset` for directories) | `hasPart` details were not checked. |
| `outputs[]` | `result` (SHOULD), `File` / `Dataset` | An output that was absent when captured has no equivalent. |
| `created_at`, `execution.duration_seconds` | `endTime` (SHOULD), `startTime` (MAY) | SciProof stores the capture time and a duration, so both times are *derived* and approximate. |
| `execution.exit_code` | `actionStatus` (MAY; completed assumed) and `error` (only with `FailedActionStatus`) | The profile does not define `exitCode`; the exact number cannot be represented. |
| `environment.env_vars` | `environment` entities (`PropertyValue`), SHOULD list non-default variables | Maps well. |
| `sha256`, `size` of files | **not defined** for files in the profile | `sha256` appears there only for `ContainerImage`. This is the core of SciProof's evidence and would need an extension property or the RO-Crate core vocabulary (not checked). |
| `compare` modes, `rtol`/`atol`, numeric snapshots, verdicts | not representable | SciProof-specific; would have to be dropped or put in a documented extension. |
| `git` (commit, branch, dirty) | not defined in the profile | Possibly via `SoftwareSourceCode` plus a version (not checked). |
| `stdout` / `stderr` | not defined | Could be `File` results if saved as files. |
| platform (OS, architecture) | not defined in the profile | |
| `agent` | `agent` (SHOULD) | SciProof deliberately does not record who ran the experiment. Omitting it is allowed (SHOULD, not MUST). |

The root data entity must also declare `conformsTo` pointing at the profile permalink.

## What would be lost

An RO-Crate produced from a manifest would say *what ran and what files were involved* but,
without an extension, would lose the part that makes SciProof useful: the hashes, the
tolerances and the verification verdict. A crate is therefore a **publication/interchange
artifact**, never a replacement for the manifest, and `sciproof verify` could not run on it.

## Recommendation

* **Do not implement an exporter yet.** A one-way export that drops the hashes is of little value,
  and an export with extension properties needs a user who will actually load it somewhere.
* If users ask: add an **export-only** command that writes `ro-crate-metadata.json` next to the
  manifest, mapping as above, keeping hashes in clearly namespaced extension properties, and
  documenting every field that is dropped. It should need no new dependencies (the file is JSON).
* Before that, check with a real consumer (for example a repository or a crate validator) that the
  0.6 profile and RO-Crate 1.3 are accepted; the changelog says 0.6 is not compatible with older
  RO-Crate versions, and tool support for it was **not verified** here.

## Open questions for users

1. Where would you load an RO-Crate (a repository, a registry, a notebook tool)?
2. Do you need the hashes in the crate, or only the description of the run?
3. Would you rather record the agent (a person or organisation) in the crate, even though the
   manifest does not contain one?
