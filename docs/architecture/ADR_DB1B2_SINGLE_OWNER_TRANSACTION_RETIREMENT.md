# ADR — DB1B2: Retire production pre-live transaction wrappers

**Status:** Implemented in development, not certified for release — 2026-10-14.
**Parent:** `OH_GOON_DB1B1_CANDIDATE.zip`, SHA-256 `634464f11b99babc35765990e1301f3db6de52daa11dda61b937f7af6187b80c`.

## Decision

One execution path; one `LocalDatabase` SQLite transaction owner. The runtime
`CausalRunJournal._transaction` and `SignerTrustEpochAuthority._transaction`
fail before work. No runtime `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK` remains in
these pre-live domain owners. X0 rejects any new unclassified transaction sites
and requires both fail-closed wrappers. The historical fixture-only transaction
semantics are confined to `backend/tests/fixtures/db1b2_legacy_sqlite.py`.
Fixture factories return the **exact production class**, with a transaction
method bound to the *test instance only*, because strict Fleet signer-epoch
publication checks the exact type. Production code must not import test fixtures.

The original `LocalDatabase.transaction(write=True)` continues to own the
single SQLite write cut. New pre-live `transfer_causal_control_atomically`
combines existing signer `require_in` and causal `transfer_control_in` inside
that original cut. Revocation and transfer cannot interleave without the
original SQLite lock and signer CAS/state checks. It is **not** an alternative
Fleet lease-transfer or effect-admission path.

The existing optional `publish_causal_admission_atomically` adds an opt-in
`operational_committed` readback callback. Only with that callback does it
return the typed `CausalAdmissionCommit` (`COMMITTED`/`ALREADY_COMMITTED`).
An apparent duplicate cannot return success from a `sqlite3.IntegrityError`
or mere matching `run_id`: replay checks the exact persisted contract bytes,
identity, signed receipt fields, control epoch, lease, active state, the
fully authenticated one-event journal and a caller-supplied operational fact.
It reopens a separate owner read transaction **after** SQLite COMMIT and
rejects false proof. The readback callback is fenced against SQL mutations
using SQLite's authorizer; this is a trusted in-process contract, not a sandbox
against arbitrary Python code replacing authorizers.

Without explicit operational proof, legacy duplicate publication remains an
error. All production detached `publish_admission_with_operation`/signer admin
wrappers reject standalone use; they cannot begin work. The original Fleet
reservation path still requires its actual `LocalDatabase` admitted write cut,
Signer grant, Enforcer admission, LACE and physical-effect rules. No signer,
admission controller, database service, schema migration, generic SQL facade,
PostgreSQL, or new SQLite engine has been added.

## Regression / acceptance

- SQLite commit, rollback, exact signed replay after an actual fresh `LocalDatabase`
  reopen, divergent receipt/epoch/lease/contract, missing operational row and
  mutating proof rejection.
- Spawned independent OS processes race signer revocation versus causal
  control transfer. Either ordering remains coherent and the stale grant
  cannot transfer after revocation.
- Real child process exit while transaction open and real child exit after
  SQLite commit but before caller acknowledgement; re-opened authoritative
  reread distinguishes noncommit from committed retry.
- Strict Fleet signer binding retains exact production runtime class, with
  compatibility preserved only in test fixtures.
- X0 mutation tests reject new legacy BEGIN and removed replay proof.
- QENV1 selects the **newest** `DB1B2_SOURCE.sha256` and both DB1B2 test
  modules in the existing CORE ring; does not add a second qualification flow.

## Invariants and deferred gates

SQLite remains authoritative. Tests on this host are development-level; native
WSL locked-uv, real disk-fault, filesystem identity and independent physical
qualification have not run here. DB2 and DB3 prove the remaining database and
Scout boundaries, DB4 performs independent locked-native and integration
qualification. Gate A/B/C, physical P3C, AG-03/04, APP PASS and release stay
**BLOCKED**. No result of this work confers permission to execute Agent steps.
