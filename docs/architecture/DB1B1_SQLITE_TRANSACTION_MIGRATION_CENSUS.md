# DB1B1 transaction migration inventory

| Boundary | Implementation | DB1B1 status |
|---|---|---|
| Live Fleet reservation | `FleetAdmissionPlanner` under `Journal._database.transaction(write=True)` | Preserved, already required in DB1A |
| Canonical optional causal admission | `LocalDatabase.publish_causal_admission_atomically` → original `CausalRunJournal.publish_admission_in` | New named operation, development-only |
| Canonical signer enrollment | `LocalDatabase.enroll_signer_trust_atomically` → `SignerTrustEpochAuthority.enroll_in` | New named operation |
| Canonical signer rotation | `LocalDatabase.rotate_signer_trust_atomically` → `SignerTrustEpochAuthority.rotate_in` | New named operation |
| Canonical signer revocation | `LocalDatabase.revoke_signer_trust_atomically` → `SignerTrustEpochAuthority.revoke_in` | New named operation |
| Legacy `CausalRunJournal._transaction` | Raw SQLite `BEGIN IMMEDIATE` / COMMIT | **Pre-live only, still unqualified; DB1B2 removal/migration pending** |
| Legacy `SignerTrustEpochAuthority._transaction` | Raw SQLite `BEGIN IMMEDIATE` / COMMIT | **Pre-live only, still unqualified; DB1B2 removal/migration pending** |
| Scout | disposable derived SQLite sidecar | Unchanged, never operational truth |

A production effect cannot begin solely because a pre-live named causal admission helper succeeded. The canonical Fleet Enforcer admission and LACE physical path remain independently required.
