# AG-02B2B2 — original Fleet Step attempt -> signed, non-effectful source commitment

**Status: HOSTED DEVELOPMENT ONLY. NO AGENT EXECUTION.**

## Canonical authority

The original `FleetRunLifecycle` now exposes an explicit **non-executable**
`reserve_original_agent_source_for_attempt()` method using the **existing**
`ExecutionSession.mutate()` transaction. It does not create a new Run, Step
scheduler, execution loop, signer, journal, database, parser, file reader or
LACE backend. Ordinary command paths remain unchanged.

The owner must satisfy, in the **same write transaction**:

1. The exact original strict enrolled Fleet run, provisioner, immutable placement,
   original execution authority and live execution session lease.
2. The original operational Journal's validated `STEP_ATTEMPT_CREATED` event,
   matching canonical operation identity, Step, repository, attempt number and
   `fleet_step_attempt_id`. Missing, ambiguous, wrong or terminalized events reject.
3. The original causal journal's active control epoch, lease, cancellation fence,
   signer epoch, signed Run admission and Agent Step handoff identity.
4. The exact frozen packet and expected identity, passed through **one existing**
   AG-02B2A decode service into **one existing** `CausalRunJournal` writer. The
   attempt digest is derived from the original operational Step attempt; a caller
   cannot choose an unrelated attempt digest.
5. Authority and cancellation are rechecked before commit. Session-owned
   transaction rollback removes the reservation on failure.

This is a developmental **source commitment**, not delivery into a sandbox.
The source bytes do not enter the journal, a model endpoint, a working tree or
an executor. No extra process or network operation occurs. Do not consume
`AGENT_SOURCE_PREWORK_RESERVED` as permission to launch work.

## Evidence and limits

New focused tests cover original attempt identity, replay, cancellation, stale
lease, changed source, missing/duplicate/terminal attempt, signed issuer
revocation and rollback. Hosted fixtures simulate enrolled trust and an
operational Journal read view; they are not independently provisioned Fleet
issuer/root evidence and do **not** qualify physical confinement.

The normal production Agent Step still cannot be admitted. **AG-02B2B3** must
add immutable, admitted, capability-bound source staging and byte-exact receipt
through the original LACE-selected backend only after containment design is
ready. AG-02B3 then extends the **original** Step sequencer without a second
runtime. AG-03, AG-04, B27, Gate A/B/C, APP PASS and release are separate gates.

**Invariant:** Resolve once, transport once, execute once, report once.
