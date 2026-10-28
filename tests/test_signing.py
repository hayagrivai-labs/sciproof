"""Signing a manifest with OpenSSH and checking the signature (needs ``ssh-keygen``)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from sciproof import cli
from sciproof.errors import ManifestError, UsageError
from sciproof.signing import (
    NAMESPACE,
    SignatureError,
    check_signature,
    sign_manifest,
    signature_path,
    ssh_keygen,
)
from sciproof.verify import Status, report_to_dict, verify

pytestmark = pytest.mark.skipif(
    ssh_keygen() is None, reason="ssh-keygen (OpenSSH) is not installed"
)


def _make_key(directory: Path, name: str = "key", principal: str = "alice@example.org"):
    key = directory / name
    subprocess.run(  # noqa: S603
        [ssh_keygen(), "-q", "-t", "ed25519", "-N", "", "-C", principal, "-f", str(key)],
        check=True,
        capture_output=True,
    )
    public = (directory / f"{name}.pub").read_text().split()
    return key, f"{principal} {public[0]} {public[1]}\n"


@pytest.fixture
def signed(project, tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    key, line = _make_key(keys)
    allowed = keys / "allowed_signers"
    allowed.write_text(line)
    project.capture()
    sign_manifest(project.manifest, key)
    project.key, project.allowed, project.keys = key, allowed, keys
    return project


def test_a_signed_manifest_verifies_and_names_the_signer(signed):
    report = verify(signed.manifest, allowed_signers=signed.allowed)
    assert report.status is Status.PASS
    check = next(c for c in report.checks if c.subject == "manifest signature")
    assert check.status is Status.PASS
    assert "good signature by 'alice@example.org'" in check.message
    assert check.evidence["namespace"] == NAMESPACE
    assert check.evidence["principal"] == "alice@example.org"
    assert check.evidence["fingerprint"].startswith("SHA256:")
    assert check.evidence["signature_file"] == "exp.sciproof.json.sig"


def test_a_good_signature_changes_what_the_report_says_is_not_checked(signed):
    data = report_to_dict(verify(signed.manifest, allowed_signers=signed.allowed))
    assert not any("unsigned" in item for item in data["not_checked"])
    assert any("vouched for this manifest" in item for item in data["not_checked"])
    assert data["signature"]["status"] == "good"
    unchecked = report_to_dict(verify(signed.manifest))
    assert any("unsigned" in item for item in unchecked["not_checked"])
    assert unchecked["signature"] is None


def test_editing_the_manifest_after_signing_fails(signed):
    data = json.loads(signed.manifest.read_text())
    data["created_at"] = "2001-01-01T00:00:00+00:00"  # every hash still matches; only a field moved
    signed.manifest.write_text(json.dumps(data, indent=2) + "\n")
    report = verify(signed.manifest, allowed_signers=signed.allowed)
    assert report.status is Status.FAIL
    check = next(c for c in report.checks if c.subject == "manifest signature")
    assert check.status is Status.FAIL
    assert "modified after signing" in check.message


def test_a_signature_from_a_key_that_is_not_allowed_fails(signed):
    other, line = _make_key(signed.keys, "other", "mallory@example.org")
    sign_manifest(signed.manifest, other)  # overwrites the signature with mallory's
    report = verify(signed.manifest, allowed_signers=signed.allowed)
    assert report.status is Status.FAIL
    assert (
        "not made by a key listed"
        in next(c for c in report.checks if "signature" in c.subject).message
    )
    allowed_for_mallory = signed.keys / "mallory_allowed"
    allowed_for_mallory.write_text(line)
    assert verify(signed.manifest, allowed_signers=allowed_for_mallory).status is Status.PASS


def test_signing_again_really_replaces_the_old_signature(signed):
    """ssh-keygen keeps the old .sig and exits 0 when it cannot ask 'Overwrite (y/n)?'."""
    before = signature_path(signed.manifest).read_bytes()
    other, line = _make_key(signed.keys, "other", "bob@example.org")
    sign_manifest(signed.manifest, other)
    after = signature_path(signed.manifest).read_bytes()
    assert after != before
    assert not signature_path(signed.manifest).with_name("exp.sciproof.json.sig.previous").exists()
    assert check_signature(signed.manifest, signed.allowed).status == "bad"  # alice no longer
    (signed.keys / "bob_allowed").write_text(line)
    assert (
        check_signature(signed.manifest, signed.keys / "bob_allowed").principal == "bob@example.org"
    )


def test_a_failed_signing_keeps_the_previous_signature(signed, tmp_path):
    before = signature_path(signed.manifest).read_bytes()
    broken = tmp_path / "broken-key"
    broken.write_text("not a private key")
    with pytest.raises(SignatureError):
        sign_manifest(signed.manifest, broken)
    assert signature_path(signed.manifest).read_bytes() == before
    assert check_signature(signed.manifest, signed.allowed).status == "good"


def test_a_missing_signature_is_inconclusive_never_a_pass(project, tmp_path):
    key, line = _make_key(tmp_path)
    allowed = tmp_path / "allowed"
    allowed.write_text(line)
    project.capture()
    report = verify(project.manifest, allowed_signers=allowed)
    assert report.status is Status.INCONCLUSIVE
    assert (
        "no signature file exp.sciproof.json.sig"
        in next(c for c in report.checks if c.subject == "manifest signature").message
    )


def test_the_signature_is_only_valid_for_the_sciproof_namespace(signed):
    """A signature made for another purpose cannot be replayed as a manifest signature."""
    other = signed.root / "other.sciproof.json"
    other.write_bytes(signed.manifest.read_bytes())
    subprocess.run(  # noqa: S603
        [ssh_keygen(), "-Y", "sign", "-f", str(signed.key), "-n", "something-else", str(other)],
        check=True,
        capture_output=True,
    )
    result = check_signature(other, signed.allowed)
    assert result.status == "bad"


def test_a_garbage_signature_file_fails_instead_of_crashing(signed):
    signature_path(signed.manifest).write_text("not a signature")
    assert check_signature(signed.manifest, signed.allowed).status == "bad"


def test_a_missing_allowed_signers_file_is_a_usage_error(signed, tmp_path):
    with pytest.raises(UsageError, match="allowed signers file not found"):
        verify(signed.manifest, allowed_signers=tmp_path / "nope")


def test_the_manifest_cannot_name_its_own_trust_root(signed):
    """Only the file given on the command line is consulted; a field in the manifest is ignored."""
    data = json.loads(signed.manifest.read_text())
    data["allowed_signers"] = "attacker"
    signed.manifest.write_text(json.dumps(data))
    assert verify(signed.manifest, allowed_signers=signed.allowed).status is Status.FAIL


def test_signing_refuses_an_invalid_manifest_and_a_missing_key(project, tmp_path):
    bad = project.root / "bad.sciproof.json"
    bad.write_text("{}")
    with pytest.raises(ManifestError):
        sign_manifest(bad, tmp_path / "key")
    project.capture()
    with pytest.raises(SignatureError, match="signing key not found"):
        sign_manifest(project.manifest, tmp_path / "missing-key")


def test_signing_reports_ssh_keygen_failures(project, tmp_path):
    project.capture()
    not_a_key = tmp_path / "not-a-key"
    not_a_key.write_text("this is not a private key")
    with pytest.raises(SignatureError, match="could not sign"):
        sign_manifest(project.manifest, not_a_key)


def test_signing_timeout_signature_error(signed, monkeypatch):
    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=60)

    monkeypatch.setattr(subprocess, "run", mock_run)
    with pytest.raises(SignatureError, match="ssh-keygen could not be run"):
        sign_manifest(signed.manifest, signed.key)


def test_without_ssh_keygen_the_check_is_inconclusive(signed, monkeypatch):
    monkeypatch.setattr("sciproof.signing.ssh_keygen", lambda: None)
    result = check_signature(signed.manifest, signed.allowed)
    assert result.status == "unavailable"
    assert verify(signed.manifest, allowed_signers=signed.allowed).status is Status.INCONCLUSIVE
    with pytest.raises(SignatureError, match="was not found"):
        sign_manifest(signed.manifest, signed.key)


def test_a_signed_rerun_is_still_a_normal_rerun(signed):
    report = verify(signed.manifest, allowed_signers=signed.allowed, rerun=True, assume_yes=True)
    assert report.status is Status.PASS
    assert any(c.subject == "manifest signature" for c in report.checks)


def test_cli_sign_and_verify(project, tmp_path, capsys):
    key, line = _make_key(tmp_path)
    allowed = tmp_path / "allowed"
    allowed.write_text(line)
    project.capture()
    assert cli.main(["sign", "exp.sciproof.json", "--key", str(key)]) == cli.EXIT_OK
    assert "Signature written to exp.sciproof.json.sig" in capsys.readouterr().out
    assert (
        cli.main(["verify", "exp.sciproof.json", "--allowed-signers", str(allowed)]) == cli.EXIT_OK
    )
    assert "good signature by 'alice@example.org'" in capsys.readouterr().out
    (project.root / "out" / "exact.txt").write_text("x")  # files changed, signature still good
    assert (
        cli.main(["verify", "exp.sciproof.json", "--allowed-signers", str(allowed)])
        == cli.EXIT_FAIL
    )
    assert (
        cli.main(["sign", "exp.sciproof.json", "--key", str(tmp_path / "nokey")]) == cli.EXIT_ERROR
    )
