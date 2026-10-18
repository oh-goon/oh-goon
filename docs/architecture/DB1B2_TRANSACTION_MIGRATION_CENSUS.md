# DB1B2 transaction migration census — 2026-10-14

| Boundary | DB1B2 disposition | Authority |
|---|---|---|
| Normal Fleet Run reservation | Preserved canonical path | Existing coordinator + `LocalDatabase` write cut |
| Signer enroll / rotate / revoke | Named owner operations | `LocalDatabase.*_signer_trust_atomically` + signer `*_in` |
| Pre-live causal admission | Named owner operation | `LocalDatabase.publish_causal_admission_atomically` + `publish_admission_in` |
| Pre-live causal control transfer | Named owner operation with signer grant fence | `LocalDatabase.transfer_causal_control_atomically` + `transfer_control_in` |
| Typed causal retry | Authenticated exact durable reread, operational fact proof | Original `LocalDatabase`, not another journal |
| Runtime causal `_transaction` | **Fail before work** | No production raw BEGIN/COMMIT |
| Runtime signer `_transaction` | **Fail before work** | No production raw BEGIN/COMMIT |
| Legacy test-only opt-in SQLite | Explicit test fixture; never imported from `backend/src` | `backend/tests/fixtures/db1b2_legacy_sqlite.py` |
| SQLite file/schema/locking/identity | Unchanged; DB2 work remains | Original `LocalDatabase` and SQLite |
| Scout | Disposable derived DB, never runtime admission | DB3 certification outstanding |

Original X0 disallows runtime transaction sites in causal and signer owners;
QENV1 selects the newest candidate manifest. Readback and fork/spawn tests
are development evidence, not physical LACE or Gate A/B/C proof.
