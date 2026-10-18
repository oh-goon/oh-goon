# T0B2B2A — Read-only native trust-custody observation (development)

**Parent:** T0B2B1. **No runtime trust/admission change. Gate A/B/C BLOCKED.**

The existing T0B2A/T0B2B1 offline reviewer authenticates evidence packets and checks
complete subject coverage. This checkpoint adds a **bounded local Linux host fact
collector** so a real custodian reviewer can attach process-principal observations
rather than relying solely on narrative statements. It is **not** a trust verifier,
independent attestor, signer, admission owner, key-custody proof or certification
observer. It never reads or outputs private key material and never starts a workload.

## Canonical ownership and use

`oh-goon-custody-host-observe` is a project-declared **offline tooling adapter**.
It reads `backend/trust-authorities.json` and its exact bound, still-pending
`backend/trust-custody-evidence.json`, but cannot change either. A caller supplies
one registered `--subject` and the PID of the **actual signer/custodian process**;
`--public-key` may name an Ed25519 *public* key file (PEM or OpenSSH) only.

From the extracted project in WSL:

```sh
cd backend
uv run --locked --group dev oh-goon-custody-host-observe \
  --subject admission_issuer_evidence/source_identity \
  --pid <ACTUAL_ISSUER_PID> \
  --public-key /path/to/issuer-public-key.pub \
  > ../T0B2B2A_SOURCE_ISSUER_LOCAL_SAMPLE.json
```

Replace the PID and public-key path with **real provisioned** identities. The
output may contain sensitive OS/process metadata: retain under reviewer-controlled
evidence storage, not in a public source archive. A local sample for the operator's
own shell is a useful diagnostic, but must **not** be cited as signer custody.

The exact policy and pending-register bytes are bound by SHA-256. A PID is bound
to the boot UUID, kernel start-time ticks (double-read) and `/proc/<pid>` directory
identity; UID/GID 4-tuples and user/mount/PID/network namespace identity links
are observed; missing, invalid, changing or inaccessible facts cause failure.
An optional public key is parsed and checked to be Ed25519, and only its raw
public-key fingerprint and file metadata are recorded. Symlink or hardlink
public-key sources are rejected.

## What this can and cannot prove

A successful local observation establishes only what the *sampling process*
could read at that instant. It does not prove possession or use of a private key,
independent custody, signer-to-PID binding, real administrator separation,
compromise isolation, root/provisioning independence, current revocation, or
absence of alternate runtime verifiers. Root/namespace identifiers alone are not
security or independence proof. A reviewer must independently correlate a real
signing challenge, service identity, OS audit, deployment configuration and the
operator's external provisioning/rotation records before making a disposition.

Every output is `classification=UNTRUSTED_LOCAL_PROCESS_OBSERVATION`,
`custody_proven=false`, `independent_custodian_proven=false`,
`effect_permission=NONE`, `runtime_authority=false`, `gate_a=BLOCKED`, and
`disposition=UNDECIDED`. An observation is NOT automatically accepted by
T0B2A/T0B2B1 intake, and cannot promote T0B2B2/T1/T2.

## One authority, anti-drift

This is not another signer, verifier, census, LACE observer or production journal
writer. X0 rejects promoting output to Gate A, granting effect permission,
skipping PID start-time verification, importing the adapter from runtime/core/
integration/service owners, or changing its project entrypoint. Focused hostile
tests are required by the original QENV1 `AGENT_PREWORK` WSL test ring.

## Required next work

1. Real field collection under the actual deployment and independent operator;
   challenge-response signing verified against the enrolled public root.
2. Corroborated root/key custody, OS identity and compromise boundaries for
   all ten Census subjects, including comparison across genuinely independent
   principals and review of conflicting signatures/claims.
3. Adjudicate KEEP/MERGE/EXTERNAL/REMOVE with authenticated external policy
   provenance. Only then propose exclusive T1 migration and separately prove Gate A.
4. Continue P1/B27 physically pinned Agent source mounting through the **same**
   original signed `RESOURCE_INTENT`/Bubblewrap/LACE owner in parallel.
