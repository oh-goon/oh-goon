# DB0 — SQLite authority census

**Parent:** exact P3B2B source `57cd625376e49e8f8535a7630963d645bb4ede4530164234ec15f2c996927405`.
**Classification:** architecture inventory, not DB4 certification.

The X0 AST guard prevents common new unowned operational SQLite transaction and connection paths. It is not a dynamic security sandbox: runtime tests still require DB4.

| File | Semantic owner | Sites |
|---|---|---:|
| `runtime/_database_policy.py` | prelaunch read-only inspection | 1 |
| `runtime/database.py` | canonical operational SQLite coordinator | 11 |
| `runtime/durable_revision.py` | canonical initialization schema helper | 1 |
| `runtime/enforcer_lace_journal.py` | pre-live causal standalone wrapper; DB1B pending | 4 |
| `runtime/enforcer_lace_signer_trust_epoch.py` | pre-live signer admin wrapper; DB1B pending | 3 |
| `runtime/exclusions.py` | canonical initialization schema helper | 1 |
| `runtime/storage_health.py` | prelaunch probe only | 6 |
| `scout/database.py` | disposable Scout sidecar | 4 |
| `scout/store.py` | disposable Scout derived schema | 3 |

## Exact static connection, transaction and schema-script sites

| File | Line | Category |
|---|---:|---|
| `runtime/_database_policy.py` | 183 | connection |
| `runtime/database.py` | 228 | transaction |
| `runtime/database.py` | 233 | transaction |
| `runtime/database.py` | 261 | transaction |
| `runtime/database.py` | 378 | transaction |
| `runtime/database.py` | 383 | transaction |
| `runtime/database.py` | 628 | connection |
| `runtime/database.py` | 633 | connection |
| `runtime/database.py` | 644 | connection |
| `runtime/database.py` | 705 | schema_script |
| `runtime/database.py` | 710 | transaction |
| `runtime/database.py` | 733 | transaction |
| `runtime/durable_revision.py` | 9 | schema_script |
| `runtime/enforcer_lace_journal.py` | 106 | schema_script |
| `runtime/enforcer_lace_journal.py` | 111 | transaction |
| `runtime/enforcer_lace_journal.py` | 114 | transaction |
| `runtime/enforcer_lace_journal.py` | 117 | transaction |
| `runtime/enforcer_lace_signer_trust_epoch.py` | 71 | transaction |
| `runtime/enforcer_lace_signer_trust_epoch.py` | 74 | transaction |
| `runtime/enforcer_lace_signer_trust_epoch.py` | 77 | transaction |
| `runtime/exclusions.py` | 12 | schema_script |
| `runtime/storage_health.py` | 155 | connection |
| `runtime/storage_health.py` | 158 | transaction |
| `runtime/storage_health.py` | 159 | transaction |
| `runtime/storage_health.py` | 169 | connection |
| `runtime/storage_health.py` | 174 | transaction |
| `runtime/storage_health.py` | 179 | connection |
| `scout/database.py` | 36 | transaction |
| `scout/database.py` | 38 | transaction |
| `scout/database.py` | 40 | transaction |
| `scout/database.py` | 77 | connection |
| `scout/store.py` | 332 | schema_script |
| `scout/store.py` | 340 | schema_script |
| `scout/store.py` | 341 | schema_script |

## Live write-cut edge

ExecutionCoordinator.reserve_fleet_with → original LocalDatabase.transaction(write=True) → FleetAdmissionPlanner.publish_atomic_fleet_prework_ingress → LocalDatabase.require_atomic_write_cut(connection) → original Journal.admit_run_in + CausalRunJournal.publish_admission_in + signer require_in/lease fence → one commit, one durable revision.

Pre-live CausalRunJournal._transaction and SignerTrustEpochAuthority._transaction remain non-production exception paths and DB1B work. storage_health and _database_policy are operator prelaunch probes, not runtime writers. Scout is disposable derivative analytics, not operational truth.

The physical Bubblewrap/Enforcer/LACE effect protocol remains outside database atomicity; SQLite commits do not prove physical teardown.

## DB1B requirements

Migrate pre-live wrappers and explicit administrative callers into the canonical owner (without changing authority), reject participant-controlled COMMIT, prove concurrent signer/lease invalidation and crash-before/after-ack idempotency; no PostgreSQL or automatic fallback.
