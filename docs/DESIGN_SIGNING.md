# Design note: signing and timestamping manifests

Status: **recommendation, no code yet.** Discussion: issue #9.

## The problem

A SciProof manifest records hashes. Hashes detect *accidental or unnoticed* changes, but they do
not stop someone who can rewrite both the manifest and the files it lists from producing
consistent, false evidence (see [SECURITY.md](../SECURITY.md)). A signature adds two statements:

1. **Authenticity**: this manifest was produced by the holder of a given key.
2. **Integrity of the manifest itself**: it has not been edited since it was signed.

A **timestamp** adds a third: the manifest existed *before* a given time.

None of this says the result is correct, that the experiment was run honestly, or that the
signer is trustworthy. A signature moves the question from "was this file changed?" to "do I
trust this key?". That is also why SciProof should not decide *whom* to trust.

## What the manifest already protects

The manifest contains the SHA-256 of every declared input and output, of the numeric snapshots
and of the stdout/stderr, so signing the manifest file transitively covers all of them. Nothing
else needs to be signed.

## Candidates compared

Facts below are from the linked primary documentation; "not verified" marks things that were not
checked.

| Option | Threat addressed | Needs | Offline | Fits SciProof's zero-dependency CLI? |
| --- | --- | --- | --- | --- |
| [`ssh-keygen -Y sign/verify`](https://man.openbsd.org/ssh-keygen.1) | Authenticity and integrity of a file, with a local list of allowed signers and an optional revocation list | OpenSSH (already on most developer machines) | Yes, only local files | Yes, as an *external* tool; SciProof would not need to implement cryptography |
| [minisign](https://jedisct1.github.io/minisign/) | Same, Ed25519, trusted comments are covered by the signature | A separate binary | Yes | As an external tool; less commonly installed than OpenSSH |
| [Sigstore](https://docs.sigstore.dev/about/overview/) (Cosign, Fulcio, Rekor) | Signing without long-lived keys, tied to an OIDC identity, plus a public transparency log | Network access to Sigstore services to sign; whether verification can be fully offline is **not verified** here | No, for signing | No: online services and a heavy dependency; interesting for a hosted workflow, not for a local-first tool |
| [RFC 3161](https://www.rfc-editor.org/rfc/rfc3161) time-stamp | Proof that a hash existed before a time, using a Time Stamping Authority. Only a hash is sent | A TSA and ASN.1 tooling | No | Not as built-in code; could be an external `openssl ts` step (**not evaluated**) |
| Signed Git commits/tags | Authenticity of a revision that contains the manifest | Git and a signing setup | Yes | Zero code; **not evaluated in detail** |

## Recommendation

1. **Do not add signing code to SciProof 0.x.** It would add cryptographic surface area and a
   trust-management problem (which keys are trusted?) that SciProof is not equipped to solve.
2. **Document a recipe using OpenSSH `ssh-keygen -Y`**, sign the manifest file as a *detached*
   signature, with a SciProof-specific namespace. This is tested below and needs nothing new.
3. Revisit built-in `sciproof sign/verify --signature` **only if users ask for it**, and then as a
   thin wrapper that calls `ssh-keygen` and never trusts a key on its own. Timestamping and
   Sigstore should wait for a concrete user with a concrete need.

## Recipe (tested with OpenSSH 10.2)

```console
# once: a key, and a file listing who you trust (principal + public key)
$ ssh-keygen -t ed25519 -f ~/.ssh/sciproof_ed25519 -C alice@example.org
$ echo "alice@example.org $(cut -d' ' -f1,2 ~/.ssh/sciproof_ed25519.pub)" > allowed_signers

# sign (writes baseline.sciproof.json.sig next to it)
$ ssh-keygen -Y sign -f ~/.ssh/sciproof_ed25519 -n sciproof-manifest@hayagrivai-labs baseline.sciproof.json

# verify: reads the manifest on stdin
$ ssh-keygen -Y verify -f allowed_signers -I alice@example.org \
    -n sciproof-manifest@hayagrivai-labs -s baseline.sciproof.json.sig < baseline.sciproof.json
Good "sciproof-manifest@hayagrivai-labs" signature for alice@example.org with ED25519 key SHA256:...
```

Observed results when this was run: the unchanged manifest verifies (exit 0); a changed manifest
fails with `incorrect signature` (exit 255); verifying with a different namespace fails with
`namespace does not match`. The namespace ("used to prevent signature confusion across different
domains of use", per the OpenSSH manual) keeps a SciProof signature from being replayed as another
kind of signature.

Always run `sciproof verify` **and** the signature check: the first checks the files against the
manifest, the second checks that the manifest is the one that was signed.

## Limits that stay true after signing

* A signature does not prove the experiment ran as described, only who vouches for the manifest.
* If the signing key is compromised, old signatures are suspect; an optional revocation list is
  supported by `ssh-keygen -Y verify -r`, but distributing it is outside SciProof.
* Without a trusted timestamp, a signer can sign an old manifest later and claim it was earlier.
* The `allowed_signers` file is the trust root; protect it like one.

## Open questions for users

* Do reviewers of your work want to verify *who* produced a manifest, or only that files are
  unchanged? If only the latter, nothing here is needed.
* Would you rely on a Git tag signature instead (the manifest committed with the code)?
