# Manifest format (schema versions 1.0 and 1.1)

A manifest is a UTF-8 JSON file named `<name>.sciproof.json`, written next to the files it
describes (its directory is the *base directory*). All paths inside it are relative to that
directory, use `/` as separator and may not contain `..`, drive letters or absolute roots.

Unknown *minor* versions (`1.x`) are read; an unknown *major* version is rejected.
Version `1.1` is written only when the manifest contains a directory (see
[Directories](#directories)); every other manifest stays at `1.0`.
Manifests are validated strictly when read (see `src/sciproof/manifest.py`), because they are
untrusted input.

```jsonc
{
  "schema": "sciproof.manifest",
  "schema_version": "1.0",
  "name": "baseline",
  "created_at": "2026-10-17T18:47:12+00:00",
  "tool": {"name": "sciproof", "version": "0.1.0"},
  "command": {"argv": ["python", "examples/experiment.py"], "cwd": "."},
  "environment": {
    "source": "command-interpreter",          // or "sciproof-process"
    "python": {"version": "3.13.1", "implementation": "CPython", "byteorder": "little"},
    "platform": {"system": "...", "release": "...", "machine": "...", "platform": "..."},
    "env_vars": {"TZ": "UTC"}                 // allowlisted, non-secret variables only
  },
  "dependencies": [{"name": "pytest", "version": "9.1.1"}],
  "git": {"commit": "...", "branch": "main", "dirty": false},   // or null
  "inputs": [{"path": "examples/params.json", "sha256": "...", "size": 58}],
  "execution": {
    "exit_code": 0,
    "duration_seconds": 0.132,
    "stdout": {"sha256": "...", "size": 56, "truncated": false, "text": "..."},
    "stderr": {"sha256": "...", "size": 0, "truncated": false, "text": ""}
  },
  "outputs": [
    {"path": "examples/out/results.json", "present": true, "sha256": "...", "size": 69,
     "compare": {"mode": "exact"}},
    {"path": "examples/out/convergence.csv", "present": true, "sha256": "...", "size": 89,
     "compare": {"mode": "numeric", "rtol": 1e-09, "atol": 0.0,
                 "snapshot": {"path": "baseline.sciproof.d/1-convergence.csv", "sha256": "..."}}}
  ]
}
```

## Field reference

Every field of a manifest. Array entries are written as `[]`.

| Field | Meaning |
| --- | --- |
| `schema` | Always `sciproof.manifest`. |
| `schema_version` | Format version. Readers accept every `1.x` and reject other major versions. |
| `name` | The experiment name given with `--name` (letters, digits, `.`, `_`, `-`, at most 100 characters). It also names the manifest file. |
| `created_at` | UTC time at which the manifest was written, which is after the command finished. |
| `tool` | Name and version of the SciProof that wrote the manifest. |
| `command.argv` | The exact argument vector of the recorded command. It is never run through a shell. |
| `command.cwd` | Always `.`: the command runs in the directory that contains the manifest. |
| `environment.source` | `command-interpreter` if the command was a Python interpreter that was asked for its own version and packages, otherwise `sciproof-process` (the interpreter running SciProof). |
| `environment.python` | Python version, implementation and byte order. |
| `environment.platform` | Operating system name, release, machine type and platform string. |
| `environment.env_vars` | Values of a fixed allowlist of non-secret environment variables that were set at capture time. |
| `dependencies` | Name and version of every package installed for that interpreter. |
| `git` | Commit, branch and whether the working tree had uncommitted or untracked changes before the run; `null` outside a Git repository. |
| `inputs` | Declared input files. |
| `inputs[].path` | Path relative to the manifest directory, using `/`. |
| `inputs[].sha256` | SHA-256 of the file as it was before and after the run. |
| `inputs[].size` | Size in bytes (of a directory: the sum of its files). |
| `inputs[].kind` | `file` (the default, written by omission) or `directory` (schema 1.1). |
| `inputs[].files` | Directories only: the regular files inside, see [Directories](#directories). |
| `execution.exit_code` | Exit status of the recorded run. A nonzero status is recorded, not hidden. |
| `execution.duration_seconds` | Wall-clock duration of the run. |
| `execution.stdout` | Standard output: SHA-256 and size of the full stream, and the first bytes as text. |
| `execution.stderr` | Standard error, recorded like `stdout`. |
| `outputs` | Declared output files. |
| `outputs[].path` | Path relative to the manifest directory. |
| `outputs[].present` | Whether the file existed after the run. |
| `outputs[].sha256` | SHA-256 of the output, or `null` if it was absent. |
| `outputs[].size` | Size in bytes (of a directory: the sum of its files). |
| `outputs[].kind` | `file` (default) or `directory` (schema 1.1). A directory is always compared `exact`. |
| `outputs[].files` | Directories only, as for inputs; empty or absent when the output was not present. |
| `outputs[].compare` | How a rerun is compared with this output: `exact` (hash) or `numeric` (tolerance for `.json`, `.csv`, `.tsv`, `.npy`). |
| `outputs[].compare.mode` | `exact` or `numeric`. |
| `outputs[].compare.rtol` | Relative tolerance of a numeric output. |
| `outputs[].compare.atol` | Absolute tolerance of a numeric output. |
| `outputs[].compare.snapshot` | Path and SHA-256 of the copy of the output in `NAME.sciproof.d/`, which is compared with the rerun. |

## Note and hardware

Two optional fields, both schema 1.1:

* `note`: free text given with `capture --note` (1 to 2000 characters, no NUL). It is for people:
  why the run was made, what to look at. It is never interpreted, and it is escaped when shown.
* `environment.hardware`, from `capture --record-hardware`:

```jsonc
"hardware": {"cpu_count": 12, "processor": "Intel64 Family 6 ...", "gpu_probe": "nvidia-smi",
             "gpus": [{"name": "NVIDIA RTX 4090", "driver": "550.54.14", "memory_mib": 24564}]}
```

  `gpu_probe` is `nvidia-smi` (the tool was found; an empty `gpus` list then means no NVIDIA GPU),
  `none` (it is not installed, so nothing is known about GPUs) or `failed`. At most 16 GPUs.
  The host name, user name, serial numbers and GPU UUIDs are never recorded.

## Claims

Since schema 1.1 a manifest may have a `claims` list (at most 100); see [Claims](CLAIMS.md):

```jsonc
"claims": [
  {"output": "metrics.json", "location": "$.accuracy", "op": ">=", "value": 0.9},
  {"output": "metrics.json", "location": "$.loss", "op": "~=", "value": 0.25,
   "rtol": 0.0, "atol": 0.01}
]
```

`output` must be a declared `.json` or `.csv` output file that was present; `location` has the
form of the difference reports (`$.key[0]` or `r2c1`); `op` is one of `==`, `!=`, `<`, `<=`, `>`,
`>=`, `~=`; `value` is a finite number for the ordering operators and `~=`, and a number, string,
boolean or `null` for `==` and `!=`; `rtol` and `atol` (finite, not negative) are present exactly
for `~=`. A manifest with `claims` and `schema_version` `1.0` is rejected.

## Directories

Since schema 1.1 an input or an exact output may be a directory. It is recorded as the sorted
list of the regular files below it:

```jsonc
{"path": "results", "kind": "directory", "present": true,
 "sha256": "<tree digest>", "size": 8,
 "files": [{"path": "a.txt", "sha256": "...", "size": 5},
           {"path": "sub/b.txt", "sha256": "...", "size": 3}],
 "compare": {"mode": "exact"}}
```

* `files[].path` is relative to the directory, uses `/`, and follows the same rules as every
  other path in a manifest. The list is sorted by the UTF-8 bytes of the path.
* The **tree digest** (`sha256`) is the SHA-256 of one line per file,
  `<file sha256>  <size>  <path>\n` (two spaces between the fields), in that order, UTF-8
  encoded. A reader recomputes it from `files` and rejects a manifest whose digest or total
  `size` does not match, so the list cannot be edited without breaking the manifest.
* Only regular files are recorded. **Empty directories are not recorded.**
* **Symbolic links, devices and other special files inside a declared directory are
  refused** at capture and reported as `FAIL` at verification: following them can leave the
  directory, and skipping them would hide changes. File names containing a backslash or that
  are not valid UTF-8 are refused too.
* At most 10,000 files per directory.
* Hidden files and build products are included; there are no ignore patterns. If the command
  writes into an *input* directory (for example `__pycache__`), capture fails because inputs
  must be read-only; declare a more specific directory or set `PYTHONDONTWRITEBYTECODE=1`.
* A declared file may not lie inside a declared directory, and the manifest, its snapshot
  directory (`NAME.sciproof.d/`) and the rerun directories cannot be declared.
* Directories cannot be compared numerically (`--output-numeric`). Single `.json`, `.csv`, `.tsv` or `.npy` files only.
* Verification reports which files were **added, removed or modified**, by name.
* A `kind` field in a manifest whose `schema_version` is `1.0` is rejected.

Older SciProof releases (0.1.x) read `1.1` manifests but do not know directories: they
report a directory input as unavailable and a directory output as missing.

## Notes

* `command.argv` is the exact argument vector. It is never run through a shell.
* `environment.env_vars` contains only variables from a fixed allowlist
  (`PYTHONHASHSEED`, `TZ`, `LANG`, `LC_ALL`, thread-count variables, `CUDA_VISIBLE_DEVICES`).
  Everything else in the environment, including secrets, is never recorded.
* `git` stores the commit, branch and whether the working tree had uncommitted or untracked
  changes *before* the run. Remote URLs are not stored because they can contain credentials.
* `stdout`/`stderr` keep the SHA-256 and size of the full stream but only the first
  `--max-log-bytes` bytes of text. Review them for secrets before sharing a manifest.
* Numeric outputs have a copy ("snapshot") in `<name>.sciproof.d/`, because a hash cannot be
  compared with a tolerance. The snapshot is itself hash-protected by the manifest. Keep that
  directory together with the manifest.
* A manifest is **not signed**. Anyone who can change both a manifest and the files it lists
  can produce consistent but false evidence. See [SECURITY.md](../SECURITY.md).
