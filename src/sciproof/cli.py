"""Command line interface.

Exit codes
----------
0  success / verification PASS
1  verification FAIL, or the captured command failed (evidence is still written)
2  verification INCONCLUSIVE
3  usage error, invalid manifest, or capture could not be completed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sciproof import __version__
from sciproof.capture import DEFAULT_ATOL, DEFAULT_MAX_LOG_BYTES, DEFAULT_RTOL, capture
from sciproof.diffmanifests import diff_manifests, format_diff_json
from sciproof.errors import ConfirmationRequired, SciProofError, UsageError
from sciproof.export import format_in_toto, format_openlineage
from sciproof.inspect_manifest import inspect_file
from sciproof.junit import list_junit, report_junit
from sciproof.ledger import (
    format_json,
    format_markdown,
    format_text,
    list_manifests,
    worst_verdict,
)
from sciproof.manifest import load_manifest
from sciproof.policy import POLICY_VERSION, load_policy
from sciproof.signing import sign_manifest
from sciproof.suggest import suggest_inputs
from sciproof.textutil import safe_text
from sciproof.update import declarations_of, describe_update, update_baseline
from sciproof.verify import (
    DEFAULT_RERUN_TIMEOUT_SECONDS,
    Status,
    format_report,
    format_report_json,
    verify,
)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_INCONCLUSIVE = 2
EXIT_ERROR = 3


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


_STATUS_EXIT = {
    Status.PASS: EXIT_OK,
    Status.FAIL: EXIT_FAIL,
    Status.INCONCLUSIVE: EXIT_INCONCLUSIVE,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="sciproof",
        description="Record computational experiments and create verifiable evidence for them.",
    )
    parser.add_argument("--version", action="version", version=f"sciproof {__version__}")
    sub = parser.add_subparsers(dest="command_name", required=True)

    cap = sub.add_parser(
        "capture",
        help="run a command once and record evidence",
        description=(
            "Run COMMAND once in the current directory and write NAME.sciproof.json. "
            "Everything after '--' is the command (it is not run through a shell)."
        ),
    )
    cap.add_argument("--name", required=True, help="experiment name (also names the manifest)")
    cap.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="PATH",
        help="declare an input file or directory (hashed before and after the run); repeatable",
    )
    cap.add_argument(
        "--output",
        action="append",
        default=[],
        metavar="PATH",
        help="declare an output file or directory compared by exact hash; repeatable",
    )
    cap.add_argument(
        "--output-numeric",
        action="append",
        default=[],
        metavar="PATH",
        help="declare a .json/.csv/.tsv output compared with a numeric tolerance; repeatable",
    )
    cap.add_argument(
        "--rtol",
        type=float,
        default=DEFAULT_RTOL,
        help=f"relative tolerance for numeric outputs (default {DEFAULT_RTOL})",
    )
    cap.add_argument(
        "--atol",
        type=float,
        default=DEFAULT_ATOL,
        help=f"absolute tolerance for numeric outputs (default {DEFAULT_ATOL})",
    )
    cap.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="stop the command after this many seconds (no evidence is written)",
    )
    cap.add_argument(
        "--max-log-bytes",
        type=int,
        default=DEFAULT_MAX_LOG_BYTES,
        help="keep at most this many bytes of stdout and of stderr in the manifest",
    )
    cap.add_argument(
        "--claim",
        nargs=2,
        action="append",
        default=[],
        metavar=("OUTPUT", "EXPRESSION"),
        help="record a checkable claim about a value in a declared .json/.csv output, for example "
        "--claim out/metrics.json '$.accuracy >= 0.9' (operators: == != < <= > >= and "
        "'~=' with rtol=/atol=); verify checks it; repeatable",
    )
    cap.add_argument(
        "--note",
        metavar="TEXT",
        help="free text stored in the manifest (why this run was made, what to look for; "
        "at most 2000 characters, never interpreted)",
    )
    cap.add_argument(
        "--record-hardware",
        action="store_true",
        help="also record the CPU count, processor and NVIDIA GPUs (via nvidia-smi); no host "
        "name, user name or serial numbers; a rerun then reports differences",
    )
    cap.add_argument("--force", action="store_true", help="overwrite an existing manifest")
    cap.add_argument(
        "--allow-nonzero",
        action="store_true",
        help="exit with status 0 even if the command failed (evidence is written either way)",
    )
    cap.add_argument(
        "cmd", nargs=argparse.REMAINDER, metavar="-- COMMAND [ARGS...]", help="the command to run"
    )

    ver = sub.add_parser(
        "verify",
        help="check a manifest (integrity by default, re-execution only with --rerun)",
    )
    ver.add_argument("manifest", type=Path)
    ver.add_argument(
        "--rerun",
        action="store_true",
        help="EXECUTE the recorded command again and compare results "
        "(runs arbitrary code with your permissions)",
    )
    ver.add_argument(
        "--yes", action="store_true", help="do not ask for confirmation before --rerun"
    )
    ver.add_argument(
        "--strict-env",
        action="store_true",
        help="with --rerun: report INCONCLUSIVE if the environment differs",
    )
    ver.add_argument(
        "--clean-env",
        action="store_true",
        help="with --rerun: give the command only a minimal environment (PATH, temp "
        "directories, numerics-related variables) instead of all of yours, including credentials",
    )
    ver.add_argument(
        "--pass-env",
        action="append",
        default=[],
        metavar="NAME",
        help="with --clean-env: also pass this environment variable; repeatable",
    )
    ver.add_argument(
        "--allowed-signers",
        type=Path,
        metavar="FILE",
        help="also check the signature <manifest>.sig against this OpenSSH allowed-signers "
        "file; a missing signature is INCONCLUSIVE, a wrong one FAIL",
    )
    ver.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_RERUN_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="stop the rerun after this many seconds (default %(default)s)",
    )

    ver.add_argument(
        "--json",
        action="store_true",
        help="print the report as one JSON object (same as --format json)",
    )
    ver.add_argument(
        "--format",
        choices=("text", "json", "markdown", "junit"),
        default=None,
        help="report format: text (default), json, markdown for CI summaries (for example: "
        ">> $GITHUB_STEP_SUMMARY) or junit XML for CI test viewers; exit codes are the same",
    )
    ver.add_argument(
        "--policy",
        type=Path,
        metavar="FILE",
        help="also check the rules of this policy file (TOML) against the evidence; see "
        "docs/POLICY.md",
    )
    ver.add_argument(
        "--save-json",
        type=Path,
        metavar="FILE",
        help="also write the JSON report to this file (whatever --format prints)",
    )

    sug = sub.add_parser(
        "suggest-inputs",
        help="run a Python command once and list files it read that are not declared",
        description=(
            "Run COMMAND once and list the files it opened for reading that are not covered "
            "by --input/--output. Only reads made by Python code are seen."
        ),
    )
    sug.add_argument("--input", action="append", default=[], metavar="PATH")
    sug.add_argument("--output", action="append", default=[], metavar="PATH")
    sug.add_argument("--timeout", type=float, default=None, metavar="SECONDS")
    sug.add_argument("cmd", nargs=argparse.REMAINDER, metavar="-- COMMAND [ARGS...]")

    sig = sub.add_parser(
        "sign",
        help="sign a manifest with an OpenSSH private key (writes <manifest>.sig)",
        description=(
            "Sign MANIFEST with the OpenSSH private key in KEY by calling ssh-keygen -Y sign. "
            "The detached signature is written to MANIFEST.sig. Check it later with "
            "'sciproof verify MANIFEST --allowed-signers FILE'."
        ),
    )
    sig.add_argument("manifest", type=Path)
    sig.add_argument("--key", type=Path, required=True, metavar="KEY", help="private key file")

    upd = sub.add_parser(
        "update",
        help="capture the recorded experiment again (re-baseline) and show what changed",
        description=(
            "Repeat the capture of MANIFEST with exactly the declarations it contains (command, "
            "inputs, outputs, tolerances, claims, note, hardware), replace the manifest, and show "
            "the differences from the old one. This EXECUTES the recorded command, like "
            "'verify --rerun', so it asks for confirmation (or needs --yes)."
        ),
    )
    upd.add_argument("manifest", type=Path)
    upd.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    upd.add_argument("--dry-run", action="store_true", help="show what would happen; run nothing")
    upd.add_argument("--note", metavar="TEXT", help="replace the note of the manifest")
    upd.add_argument("--timeout", type=float, default=None, metavar="SECONDS")

    dif = sub.add_parser(
        "diff",
        help="compare two manifests: what changed between two recorded runs? (executes nothing)",
        description=(
            "Compare OLD and NEW manifests: command, environment (Python, platform, packages), "
            "Git state, inputs, outputs (numeric outputs with the OLD tolerance) and exit code. "
            "Exit status 0 if there is no difference, 1 if there is. Nothing is executed."
        ),
    )
    dif.add_argument("old", type=Path, help="the baseline manifest")
    dif.add_argument("new", type=Path, help="the manifest to compare with it")
    dif.add_argument("--json", action="store_true", help="same as --format json")
    dif.add_argument("--format", choices=("text", "json", "markdown"), default=None)

    lst = sub.add_parser(
        "list",
        help="list the manifests in a directory; with --check, verify all of them (no rerun)",
        description=(
            "List the *.sciproof.json files in DIRECTORY (default: the current directory), newest "
            "first. With --check each one is verified for integrity (nothing is executed) and the "
            "exit status is 0 if all PASS, 1 if any FAIL or is invalid, 2 if any is INCONCLUSIVE."
        ),
    )
    lst.add_argument("directory", nargs="?", type=Path, default=Path("."))
    lst.add_argument("-r", "--recursive", action="store_true", help="also look in subdirectories")
    lst.add_argument("--check", action="store_true", help="verify every manifest (integrity only)")
    lst.add_argument("--json", action="store_true", help="same as --format json")
    lst.add_argument("--format", choices=("text", "json", "markdown", "junit"), default=None)

    exp = sub.add_parser(
        "export",
        help="write a manifest in another format (in-toto Statement v1, OpenLineage events)",
        description=(
            "Write MANIFEST as an in-toto Statement v1 with a SciProof predicate. The Statement is "
            "not signed and is not an attestation by itself; wrap it with your own tooling."
        ),
    )
    exp.add_argument("manifest", type=Path)
    exp.add_argument(
        "--format",
        choices=("in-toto", "openlineage"),
        default="in-toto",
        help="in-toto: an unsigned Statement v1 with hashes; openlineage: START and "
        "COMPLETE/FAIL run events with the inputs and outputs (no hashes, no verdict)",
    )
    exp.add_argument(
        "-o", "--output", type=Path, metavar="FILE", help="write here (default: stdout)"
    )

    pol = sub.add_parser(
        "check-policy",
        help="validate a policy file and list its rules (nothing is verified)",
    )
    pol.add_argument("policy", type=Path)

    ins = sub.add_parser("inspect", help="show a readable summary; never executes anything")
    ins.add_argument("manifest", type=Path)
    return parser


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        raise ConfirmationRequired(
            "refusing to execute the recorded command: no terminal to ask for confirmation; "
            "re-run with --yes after reading the command (see 'sciproof inspect')"
        )
    print(prompt, file=sys.stderr)  # stderr keeps stdout clean for --json
    answer = input("\nExecute this command? Type 'yes' to continue: ")
    return answer.strip().lower() == "yes"


def _run_capture(args: argparse.Namespace) -> int:
    command = list(args.cmd)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SciProofError(
            "no command given; usage: sciproof capture --name NAME [options] -- COMMAND"
        )
    result = capture(
        name=args.name,
        argv=command,
        base=Path.cwd(),
        inputs=args.input,
        outputs=args.output,
        numeric_outputs=args.output_numeric,
        rtol=args.rtol,
        atol=args.atol,
        timeout=args.timeout,
        max_log_bytes=args.max_log_bytes,
        force=args.force,
        claims=[(output, expression) for output, expression in args.claim],
        note=args.note,
        record_hardware=args.record_hardware,
    )
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    manifest = result.manifest
    print(f"Evidence written to {result.manifest_path.name}")
    print(f"  command exit code: {result.exit_code}")
    print(f"  inputs recorded:   {len(manifest['inputs'])}")
    print(f"  outputs recorded:  {len(manifest['outputs'])}")
    print(
        "Note: stdout/stderr are stored in the manifest; check them for secrets before sharing it."
    )
    if result.exit_code != 0:
        print(
            f"The command failed (exit code {result.exit_code}); the failure was recorded.",
            file=sys.stderr,
        )
        return EXIT_OK if args.allow_nonzero else EXIT_FAIL
    return EXIT_OK


def _run_suggest(args: argparse.Namespace) -> int:
    command = list(args.cmd)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SciProofError("no command given; usage: sciproof suggest-inputs [options] -- COMMAND")
    found = suggest_inputs(command, Path.cwd(), args.input + args.output, args.timeout)
    if found.exit_code != 0:
        print(f"warning: the command exited with status {found.exit_code}", file=sys.stderr)
    if not found.hook_loaded:
        print(
            "inconclusive: the audit hook never started, so no file reads were observed. The "
            "command is probably not Python, or Python ran with -I or -S (which ignore "
            "PYTHONPATH). Nothing can be said about undeclared inputs.",
            file=sys.stderr,
        )
        return EXIT_INCONCLUSIVE
    for path in found.inside:
        print(f"suggest --input {safe_text(path)}")
    for path in found.outside:
        print(f"read outside the working directory (cannot be declared): {safe_text(path)}")
    if not (found.inside or found.outside):
        print("no undeclared files were read by Python code")
    print(
        "Note: only reads made by Python code are seen (not C extensions, other programs, "
        "network or environment variables)."
    )
    return EXIT_OK


def _report_format(args: argparse.Namespace) -> str:
    """``--json`` is an alias of ``--format json``; asking for two different formats is an error."""
    if args.json and args.format not in (None, "json"):
        raise UsageError(f"--json conflicts with --format {args.format}")
    return args.format or ("json" if args.json else "text")


def _run_check_policy(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    rules = [
        f"  {section}.{key} = {value!r}"
        for section, content in policy.rules.items()
        for key, value in content.items()
    ]
    digest = policy.sha256[:16]
    print(
        f"policy {safe_text(policy.file)} is valid (version {POLICY_VERSION}, sha256 {digest}...)"
    )
    print("\n".join(rules) if rules else "  (no rules: every verification passes this policy)")
    return EXIT_OK


def _run_list(args: argparse.Namespace) -> int:
    output_format = _report_format(args)
    entries = list_manifests(args.directory, recursive=args.recursive, check=args.check)
    if output_format == "junit":
        print(list_junit(entries), end="")
    else:
        printer = {"json": format_json, "markdown": format_markdown}.get(output_format, format_text)
        print(printer(entries, args.check))
    if not args.check:
        return EXIT_OK
    return {"PASS": EXIT_OK, "FAIL": EXIT_FAIL, "INCONCLUSIVE": EXIT_INCONCLUSIVE}.get(
        worst_verdict(entries) or "PASS", EXIT_OK
    )


def _write_user_file(path: Path, text: str) -> None:
    """Write a file the user named; an unwritable path is an error (exit 3), not a traceback."""
    try:
        path.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise SciProofError(f"cannot write {path}: {reason}") from exc


def _run_export(args: argparse.Namespace) -> int:
    printer = format_openlineage if args.format == "openlineage" else format_in_toto
    text = printer(args.manifest)
    if args.output is None:
        print(text, end="")
        return EXIT_OK
    _write_user_file(args.output, text)
    print(f"Wrote {args.output}")
    return EXIT_OK


def _run_sign(args: argparse.Namespace) -> int:
    target = sign_manifest(args.manifest, args.key)
    print(f"Signature written to {target.name}")
    print(
        "Note: a signature shows that the holder of the key vouched for this exact manifest. "
        "Check it with 'sciproof verify MANIFEST --allowed-signers FILE'."
    )
    return EXIT_OK


def _run_update(args: argparse.Namespace) -> int:
    if args.dry_run:
        manifest = load_manifest(args.manifest)
        print(describe_update(args.manifest.resolve(), declarations_of(manifest)))
        return EXIT_OK
    result = update_baseline(
        args.manifest,
        assume_yes=args.yes,
        confirm=_confirm if not args.yes else None,
        note=args.note,
        timeout=args.timeout,
    )
    if result is None:
        print("declined; nothing was executed and nothing was changed", file=sys.stderr)
        return EXIT_INCONCLUSIVE
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(
        f"Updated {result.capture.manifest_path.name} "
        f"(command exit code {result.capture.exit_code})"
    )
    print()
    print(result.diff.to_text())
    if result.capture.exit_code != 0:
        print("The command failed; the failure was recorded.", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


def _run_diff(args: argparse.Namespace) -> int:
    output_format = _report_format(args)
    report = diff_manifests(args.old, args.new)
    if output_format == "json":
        print(format_diff_json(report))
    elif output_format == "markdown":
        print(report.to_markdown())
    else:
        print(report.to_text())
    return EXIT_OK if report.identical else EXIT_FAIL


def _run_verify(args: argparse.Namespace) -> int:
    output_format = _report_format(args)  # before anything runs
    report = verify(
        args.manifest,
        rerun=args.rerun,
        assume_yes=args.yes,
        strict_env=args.strict_env,
        timeout=args.timeout,
        confirm=_confirm if args.rerun and not args.yes else None,
        clean_env=args.clean_env,
        pass_env=args.pass_env,
        allowed_signers=args.allowed_signers,
        policy=args.policy,
    )
    if output_format == "json":
        print(format_report_json(report))
    elif output_format == "markdown":
        print(report.to_markdown())
    elif output_format == "junit":
        print(report_junit(report), end="")
    else:
        print(format_report(report))
    if args.save_json is not None:  # after the verdict is printed, so a bad path never hides it
        _write_user_file(args.save_json, format_report_json(report) + "\n")
    return _STATUS_EXIT[report.status]


def _make_output_robust() -> None:
    """Never crash because the terminal cannot encode a character from a manifest or a log.

    Captured stdout/stderr and file names can contain any character, while a console may use
    a narrow encoding (for example cp1252 on Windows). Unencodable characters are printed as
    escapes (``\u2713``) instead of raising ``UnicodeEncodeError``.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    _make_output_robust()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command_name == "capture":
            return _run_capture(args)
        if args.command_name == "verify":
            return _run_verify(args)
        if args.command_name == "diff":
            return _run_diff(args)
        if args.command_name == "update":
            return _run_update(args)
        if args.command_name == "check-policy":
            return _run_check_policy(args)
        if args.command_name == "list":
            return _run_list(args)
        if args.command_name == "export":
            return _run_export(args)
        if args.command_name == "sign":
            return _run_sign(args)
        if args.command_name == "suggest-inputs":
            return _run_suggest(args)
        print(inspect_file(args.manifest))
        return EXIT_OK
    except SciProofError as exc:
        print(f"error: {safe_text(str(exc), 2000)}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_ERROR
    except EOFError:
        print("error: no confirmation received", file=sys.stderr)
        return EXIT_ERROR
