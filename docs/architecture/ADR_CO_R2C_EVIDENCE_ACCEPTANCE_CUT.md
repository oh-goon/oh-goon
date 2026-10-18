# ADR CO-R2c — Persisted lease expiry at original evidence acceptance cut

**State:** Accepted for development. **Parent:** CO-R2A exact complete source SHA-256 `b82e2a35568c36784e4ef812a15dbecd6ee44e8059c604902ded559c1fa45e23`. **Not a release, native LACE or signer certification.**

## Failure and decision

`ExecutionEvidenceAcceptanceAuthority.accept()` performed physical/quiescence and lease prechecks, then revalidated durable lease `ACTIVE`, generation and materialization lineage in the original `LocalDatabase` write transaction. But it did not inspect persisted `expires_at` there. A lease could expire after the precheck and still admit evidence; the receipt's `accepted_at` clock was sampled before the original transaction.

The same existing authority now fetches durable `issued_at` and `expires_at` in its already-serialized write cut. After all durable ownership/lineage/producer checks it takes one UTC clock sample. The persisted timing must parse as aware timestamps and equal the caller's immutable lease timing, with no extension by a stale/corrupt snapshot. If the sample is at or after expiration, it raises `ExecutionEvidenceRejected` without an `execution_evidence_acceptances` insert. The same sample is the receipt's `accepted_at` and the insert uses the original transaction and uniqueness constraint. No lease is renewed, retired, signed, or independently redecided.

## Failure cuts and evidence

- **Preflight -> cut expiry:** The late sampled clock rejects and the database remains without a receipt.
- **Boundary equality:** `accepted_at == expires_at` rejects; not a grace period.
- **Durable timing replacement:** mismatched or unparsable persisted timestamp rejects; do not adopt a new lease epoch through an old reference.
- **Stale generation:** the existing durable tuple check rejects, without acceptance.
- **After INSERT, before COMMIT failure:** original SQLite rollback preserves no receipt; retry can succeed if still valid.
- **After COMMIT, before ACK failure:** the existing UNIQUE(materialization_id) prevents a second acceptance even with a different result identity. Recovery rereads the original authoritative receipt; no new retry worker.
- **Writer/quiescence:** original `WorkspaceLeaseStore.require_writer_quiescence` and original materialization/lineage proofs are retained. Focused ledger tests hold that preflight constant; they do not qualify independent physical writer absence.

X0 source mutation guards enforce original transaction/clock/insert call edges and original QENV1 enrolls two new files into CORE, without another qualification runner. Time sampling is the existing model's write-cut choice (as in `WorkspaceLeaseStore._finish`), not a claim that wall-clock time and a SQLite commit are physically atomic. A stalled host, clock correction or delayed COMMIT still requires native conformance/operational policy in CO-R3; do not promote this development test to physical certification.

## Non-goals and remaining gates

Do not introduce a new evidence ledger, scheduler, database, expiry lease owner, physical quiescence publisher, signature, receipt replay engine or silent success after an unknown commit. CO-R2b writer-bearing Git retirement, native WSL fault-conformance, independent LACE/Enforcer A/B/C, AG-03/04, APP PASS and formal release remain open. CO-R3 is next coherent implementation/qualification bundle; CO-R4 remains deferred.
