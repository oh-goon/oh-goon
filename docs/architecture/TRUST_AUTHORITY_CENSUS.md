# Trust Authority Census — T0A source inventory

**Status:** DESIGN-ONLY source inventory; native deployment custody NOT observed; Gate A BLOCKED.
**Source of policy intent:** `backend/trust-authorities.json`; this document is a generated projection.
**Existing runtime:** unchanged; T1 exclusive migration and authenticated bootstrap NOT implemented.

## Observed cryptographic mechanisms

| Family | Issuance / verification | Distinct keys required today | Independent custody proven? |
|---|---|---:|---|
| Admission issuer evidence | Ed25519 / `TrustedIssuerVerifier.verify` | 6 | NO |
| Control-plane receipts | HMAC-SHA256 / `ReceiptAuthenticator.issue/verify` | one operational key identity (deployment not observed) | NO |
| Signer epoch | SQLite original admission transaction, not a new signer | 0 | Native Gate A pending |

## Current field-specific issuer claims (not six proven semantic authorities)

| Claim | Status | Custodian | Proposed disposition |
|---|---|---|---|
| `backend_capability_manifest_identity` | UNREVIEWED | NOT_OBSERVED | UNDECIDED (KEEP/MERGE/EXTERNAL/REMOVE) |
| `execution_environment_identity` | UNREVIEWED | NOT_OBSERVED | UNDECIDED (KEEP/MERGE/EXTERNAL/REMOVE) |
| `lock_identity` | UNREVIEWED | NOT_OBSERVED | UNDECIDED (KEEP/MERGE/EXTERNAL/REMOVE) |
| `native_tool_identity` | UNREVIEWED | NOT_OBSERVED | UNDECIDED (KEEP/MERGE/EXTERNAL/REMOVE) |
| `repository_identity` | UNREVIEWED | NOT_OBSERVED | UNDECIDED (KEEP/MERGE/EXTERNAL/REMOVE) |
| `source_identity` | UNREVIEWED | NOT_OBSERVED | UNDECIDED (KEEP/MERGE/EXTERNAL/REMOVE) |

## Control-plane signed receipt domains (shared HMAC signer)

`admission`, `admission-signer-epoch`, `agent-source-prework`, `agent-source-prework-stage`, `agent-tool-launch-grant`, `recovery-transfer`, `step-output`, `terminal`, `unresolved`

## Blocked external evidence before authority-minimal migration

For EACH issuer/root: independently establish semantic owner, signing principal, public-root owner,
OS UID/process custody, compromise boundary, benefit over domain separation, rotation/revocation
and external provisioning. Source code cannot establish independent deployment custody.
A public key file is not proof that its private signing key has independent custody.
No key/root count can be reduced until T0B owner review and T1 single-path migration are complete.
Never infer Gate A, physical Gate B or agent effect permission from this report.
## T0B — deployment custody and authority review

**Review state:** PENDING independent deployment evidence; Gate A BLOCKED.
This table is projected from `backend/trust-custody-evidence.json` and bound by SHA-256
to the only trust authority registry (`backend/trust-authorities.json`).
The evidence register is NOT an authenticated runtime policy, authority or signer.

| Exact subject | Custody | Disposition | Independent evidence |
|---|---|---|---|
| `admission_issuer_evidence/backend_capability_manifest_identity` | UNREVIEWED | UNDECIDED | NOT PROVIDED |
| `admission_issuer_evidence/execution_environment_identity` | UNREVIEWED | UNDECIDED | NOT PROVIDED |
| `admission_issuer_evidence/lock_identity` | UNREVIEWED | UNDECIDED | NOT PROVIDED |
| `admission_issuer_evidence/native_tool_identity` | UNREVIEWED | UNDECIDED | NOT PROVIDED |
| `admission_issuer_evidence/repository_identity` | UNREVIEWED | UNDECIDED | NOT PROVIDED |
| `admission_issuer_evidence/source_identity` | UNREVIEWED | UNDECIDED | NOT PROVIDED |
| `control_plane_receipts` | UNREVIEWED | UNDECIDED | NOT PROVIDED |
| `future_role/independent_physical_observer` | UNREVIEWED | UNDECIDED | NOT PROVIDED |
| `future_role/provisioning_root` | UNREVIEWED | UNDECIDED | NOT PROVIDED |
| `signer_trust_epoch` | UNREVIEWED | UNDECIDED | NOT PROVIDED |

Independent review must establish every semantic owner, private/public-key custodian, OS
principal, compromise boundary, security benefit over domain separation, rotation,
revocation and provisioning before T1 can change any original trust requirement.
No key material or secrets belong in this repository or review register.
A source-file assertion, code signature, key path or owner name is NOT physical custody proof.
A later checkpoint must authenticate externally collected evidence and explicitly approve
the authority migration; this checkpoint intentionally cannot mark any subject VERIFIED.
