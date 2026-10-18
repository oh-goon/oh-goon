# DB1B1 — Named atomic SQLite operations within the original owner

Status: DEVELOPMENT implementation, not DB4 certification, not release.
Parent: DB0DB1A source SHA-256 `1a55f6edc835095fa94548f74de6a67ac9dc9a6168194ce333f7f8c126eb2a8f`.

## Decision

`LocalDatabase` remains the only operational SQLite transaction coordinator. It exposes named operations for existing optional causal admission and signer trust enrollment, revocation and rotation. Domain validation stays in the original `CausalRunJournal.publish_admission_in` and `SignerTrustEpochAuthority.*_in` participants. The original live Fleet reservation still owns its single admitted write transaction; no second admission entrypoint, trust resolver, launcher or execution path is authorized by the pre-live causal helper.

New named operations require the exact original owner-admitted write cut. During each new operation, a bounded SQLite authorizer rejects a participant-initiated `BEGIN`, `COMMIT` or `ROLLBACK`; the owner removes this guard and alone commits or rolls back. The authorizer protects against accidental participant transaction endings, not malicious in-process code able to call `set_authorizer` itself. It is not an OS sandbox or signer/custodian boundary.

An identical logical command retried after commit may fail due to a unique constraint or stale CAS; that **does not** imply idempotent replay success. Durable-read recovery must inspect the original committed truth before deciding whether to acknowledge a retried command.

SQLite remains the one configured operational engine. The two existing standalone `_transaction` methods are retained and *classified pre-live legacy* for compatibility with historical fixtures; they are **not** newly qualified as runtime persistence authorities. Neither Scout nor Game Tape owns admission.

## Implementation limits

- No engine swap, ORM, generic `execute(sql)` interchange layer, PostgreSQL implementation or automatic fallback.
- Optional causal and signer schemas are still provisioned before the operational database identity is admitted; schema administration migration is not completed by DB1B1.
- DB1B2 must move remaining explicit production-compatible admin/test callers off the standalone transaction wrappers and close their exception in the X0 census. Do not silently promote a legacy path into live operation.
- DB2: real crash/restart/durability and concurrency evidence under locked SQLite runtime. DB4 remains an independent native acceptance gate.
- Gate A/B/C, P3C, AG-03/04, APP PASS and release remain BLOCKED.

## Required regression classes

1. Enrollment, rejected second provisioning, revocation and stale CAS under two `LocalDatabase` owners of the same SQLite file.
2. Full rollback of signer mutations on failure and original causal/operational journal publication on simulated crash before commit.
3. Durable reread and duplicate-command rejection after hypothetical lost acknowledgement.
4. Actual SQLite denial of COMMIT/ROLLBACK via both connection methods and SQL statements inside the named operation.
5. X0 mutation rejection of a bypassed cut proof, removed owner binding, missing authorizer or a new SQLite writer.
6. QENV1 CORE selection for the new tests, exact source identity and no native certification promotion from hosted tests.
