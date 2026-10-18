# ADR — CO-R3P original Fleet live signer-epoch physical-effect fence

Status: IMPLEMENTED FOR EXISTING TRANSACTIONAL RESOURCE INTENT; DEVELOPMENT ONLY, NOT A RELEASE.
Parent: CO-R3O SHA-256 `1c59630652e563c4614ec8c2b712bda605571d481f633e91f6a2ca081cf31496`.

## Problem

The original `FleetRunLifecycle.require_execution_authority_in` compared the
immutable Run/placement projection and the current executor-pool grant. The
`OriginalFleetLocalLaceReservation.reserve` callback uses that method inside
its original SQLite write transaction both before and after signed resource
intent publication. A durable signer trust-epoch revoke changes the signer
row but need not alter immutable executor placement, leaving a missing
pre-resource trust check on this canonical effect-adjacent path.

## Decision

Extend **that same** `FleetRunLifecycle.require_execution_authority_in` for
authenticated strict Fleet Runs only. In the original transaction:

1. Preserve exact executor capacity, placement, Run and repository authority check.
2. Resolve the already-enrolled original `FleetPreworkProvisioner` and exact
   `StrictFleetPolicyEnrollment`, reject missing or substituted enrollment.
3. Recheck its frozen/current backend environment identity.
4. Read the *only* existing `lace_signer_trust_epoch` row and require its ACTIVE
   epoch, roots and current native deployment signer-policy snapshot using
   `SignerTrustEpochAuthority.require_in` on the **same connection**.
5. Verify the original MAC-bound `RUN_SIGNER_EPOCH_BOUND` from the original
   `CausalRunJournal`, bound to this Run and active signer grant.

The original resource-intent reservation already calls this method before
and after tentative publication. If an epoch changes inside that write cut,
the entire provisional resource-intent transaction rolls back. An external
SQLite revoke waits for the original BEGIN IMMEDIATE transaction; that
cannot retroactively revoke a transaction already committed. Future physical
effect cuts must separately enforce their established original authority.

No new signer, trust table, root resolver, journal, issuer, local LACE owner,
executor path, permit, or independent custodian is introduced.

## Regression proof and limits

- Hosted: real original admission and signed causal Run fixture; native policy
  and same-DB signer row; active permit; durable revoke; changed generation;
  missing/revoked row; signed journal corruption; trust change between the
  two owner checks, leaving no committed trust mutation or resource intent.
  The fixture substitutes only the Direct-versus-Strict type selector; it
  does not claim end-to-end real Strict Run execution.
- Architecture: X0 source mutants reject removing the original late-cut
  signer check, signed Run epoch check, environment consistency or CORE
  enrollment. QENV1 exact-source selector includes CO-R3P.
- Read-only LACE negative controls ensure a local empty cgroup observation
  cannot mint independent observer custody, effect permission or complete
  foreign FD/mount/namespace/escaped writer absence.
- Native cgroup-v2 execution, real signer administrative separation,
  authenticated external observer custody, post-revocation process escape,
  independent parent mutation authority and complete writer absence have
  NOT been demonstrated by this hosted checkpoint.
- Linux `rmdir(name, dir_fd=...)` still cannot atomically predicate deletion
  on the target inode. CO-R2b writer-bearing worktree removal and release stay
  BLOCKED.
