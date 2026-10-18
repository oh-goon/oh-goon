# T0B1 — Custody evidence intake boundary

**Status:** Implemented design-time contract; real custodian review **NOT COMPLETE**; Gate A **BLOCKED**.
**Source parent:** `OH_GOON_T0A_CANDIDATE.zip` (source SHA-256 `814ab2eeab91ab51959450636fa40d18ee7103b75ade2a6716960eb240a5b09a`).

## Authority decision

`backend/trust-authorities.json` is the only machine-readable *inventory of trust authority*. It is source-backed, and is **not yet deployed/authenticated runtime trust policy**. There is exactly one generated human census, `docs/architecture/TRUST_AUTHORITY_CENSUS.md`.

`backend/trust-custody-evidence.json` is a deliberately non-authoritative, exact-registry-hash-bound **pending evidence intake**, not a second root policy or an authorization API. It lists all six current Ed25519 admission-claim subjects, the original HMAC receipt owner, original database epoch owner, and currently missing provisioning and independent-observer attestation roles.

Each subject asks for the same independent evidence: semantic owner, signing/private-key custodian, verification-root custodian, OS/process principal, independently bounded compromise, real security benefit distinct from domain separation, why domain separation cannot suffice, key rotation and revocation, and original provisioning custody.

Each question currently reads `NOT_OBSERVED`, each decision `UNDECIDED`, and each status `UNREVIEWED`. No source code or generated markdown can verify real OS/administrative custody.

## Trust boundaries

- The current six-claim admission verifier, HMAC issuer/verifier, signer-epoch authority, and Run admission remain **bit-for-bit unchanged**.
- The T0B register is read by **only** the existing repository architecture gate and the one source-backed census projector.
- The original architecture checker fails on missing/mismatched subjects, evidence-registry digest drift, a forged custodian, unreviewed `KEEP/MERGE/REMOVE/EXTERNAL`, a manufactured `VERIFIED`/Gate A PASS, or a runtime attempting to read the non-authoritative evidence as trust policy.
- A later T0B2 must explicitly authenticate **external**, actually custodied evidence and approve dispositions under a separately reviewed assurance process. This design-time source checkpoint cannot automatically become self-validating by changing JSON fields.
- T1 must change the existing Enforcer verifier in one exclusive, fail-closed cutover. No extra agent signer, trust daemon, issuer, Fleet admission or parallel verification path.

## Evidence required to finish T0B2

For each subject obtain an externally supplied key/root identity or evidence reference; an accountable real-world custodian; signed or independently auditable OS/process principal observation; demonstrated compromise isolation; role/domain permissions; rationale why current root must survive/merge/be external; rotation and revocation procedure with executed test receipts; policy/schema/version provenance and honest attestation strength. **Do not put private keys or secrets in repository evidence.**

An owner name, source-code constant, test key, certificate pathname or registry assertion is not proof of independent deployment custody. T0B2 approval, T1 migration and T2 native Gate A are distinct gates.

## Verifications

Enrolled in the existing `check_architecture` / `make qualify-development-wsl` `AGENT_PREWORK` ring. No standalone execution path; QENV1's prepare-once environment policy unchanged. Host test PASS cannot imply native custody, Gate A, physical B/C, Agent egress safety, APP PASS or release.
