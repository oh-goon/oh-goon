# The Scout — 977.0.0 release validation

This file records validation for the Scout privacy and deployment-boundary hardening release.

## Verified architecture and behavior

- Scout remains read-only and outside execution/admission/scheduling/retry/cancellation/placement ownership.
- Canonical execution state remains in the ordinary `LocalDatabase` SQLite file.
- Scout-derived observations live only in the optional adjacent `*.scout.sqlite3` analytical sidecar.
- Scout materialization completes while the canonical database holds an active write transaction, proving Scout cannot delay execution through the canonical SQLite writer lock.
- Disabled Scout creates no analytical sidecar.
- Scout's processed Journal revision is monotonic in the sidecar; a stale worker cannot overwrite newer derived state.
- Unchanged Journal revisions return without reconstructing the canonical Run-detail view.
- Changed Run identities incrementally refresh bounded decoded Scout windows; unrelated revisions reuse the exact immutable observations and only advance cache revision identity.
- Scout derived-table privacy remains enforced by explicit metadata/resource allowlists.
- `OBS-001` rejects GitLab coupling inside Scout-owned backend modules.
- Generated OpenAPI/frontend contracts are current.
- Release identity is consistent at **977.0.0**.


## Privacy/deployment proof added in 977

- Scout read transactions are enforced with SQLite `query_only`.
- Scout database, WAL and SHM files are owner-only (`0600`) on POSIX and are re-hardened on reopen. Hardening tolerates SQLite concurrently removing transient WAL/SHM files and uses a no-follow file-descriptor path where supported, so permission maintenance neither crashes concurrent readers nor follows a substituted symlink.
- Existing symlink/non-regular sidecar-family paths are rejected before SQLite access; regression tests prove a symlink target is not modified.
- `PRAGMA trusted_schema = OFF` is applied to Scout connections and `PRAGMA secure_delete = ON` to Scout writes.
- Hourly retention maintenance truncates the Scout WAL after logical retention work; analytical reset checkpoints the WAL too.
- A genuinely corrupt/non-database Scout sidecar is disposable: it is removed and recreated, then repopulated from canonical Journal facts. Canonical durable revision remains unchanged during HTTP recovery.
- Scout health reports analytical storage bytes for the whole database/WAL/SHM family.
- Restart regression preserves processed Journal revision and reasserts private sidecar mode.

## Test evidence

- **31 core Scout analytics/measurement/finding/scale tests pass.**
- **36 Scout Journal/store/privacy/service/concurrency tests pass.**
- **7 Scout HTTP integration tests pass.**
- **5 Scout-specific repository-tooling/load-contract tests pass.**
- **79 Scout-focused tests pass in total.**
- **357 complete runtime unit tests pass**, covering the generic Journal/storage/runtime surface affected by the unchanged-revision fast path and Scout sidecar integration.
- Repository architecture checks pass, including the Scout observer boundary, sidecar ownership and canonical frontend read ownership.
- Static EN/SV i18n parity passes: **2,075 keys**.
- The production frontend build and theme contrast checks pass.
- Theme contrast contract passes: **22 required pairs >= 4.5:1**; weakest required pair **4.88:1**.
- Browser quality contract passes its dependency-independent journey/visibility/accessibility/theme wiring checks.

## Deterministic active-league load probe

The default `scripts/scout_load_probe.py` / `make scout-load-probe` workload uses 5,000 repositories, four Step observations per repository, 500 concurrent readers and ten active revision cycles changing 50 repositories per cycle.

Final sandbox run for 977.0.0:

```text
repositories:                     5,000
Steps/repository:                      4
Step observations:                20,000
concurrent readers:                   500
revision cycles:                       10
changed repositories/cycle:            50

overview projection:             ~467.298 ms
CPU-outlier projection:           ~184.660 ms
initial sidecar materialization:   ~411.806 ms
revision refresh p50:               ~29.887 ms
revision refresh p95:               ~39.223 ms
revision refresh total (10):       ~293.283 ms
500 cached reads total:             ~24.738 ms
cached read p50:                      ~0.004 ms
cached read p95:                      ~0.011 ms
snapshot cache misses:                     1
snapshot cache hits:                     510
```

These are **local deterministic probe measurements, not production SLOs**. The command exists so deployment-equivalent hardware can repeat the same workload and compare observed behavior.

## Failure-isolation proof strengthened in 976

The 975 release kept Scout logically outside execution but stored derived Scout rows in the same SQLite file as canonical execution state. Although those writes did not advance canonical durable revision, SQLite has one writer lock per database file, so an analytical `BEGIN IMMEDIATE` could theoretically delay a canonical execution write.

976 removes that residual coupling. Scout now writes only to its adjacent disposable sidecar. A regression deliberately holds the canonical database write lock and proves Scout materialization still completes. The sidecar contains no execution authority and can be deleted/rebuilt from canonical Journal evidence.

## Environment-limited browser validation

The extracted sandbox does not contain frontend `node_modules`, and the pinned npm dependency installation cannot complete in this network-restricted environment. Therefore the complete React/Vite/Vitest/Playwright release suite cannot execute here.

Scout Playwright acceptance journeys remain checked in. A deployment must install the exact dependencies declared by `frontend/package.json` and run the normal frontend/browser release gates before claiming browser-runtime certification.

This limitation does not affect the execution-isolation proof: execution never depends on Scout, the Scout sidecar, or Scout frontend validation.

## Multi-user authorization status

Oh Goon now has one process-local access capability protecting the complete local `/api` transport, but it still has no multi-user User/Tenant/repository-visibility authority. Scout therefore does **not** invent Scout-specific RBAC. A future multi-user server release must filter every Scout read through a central product authorization owner rather than treating the local-access capability as repository visibility policy.
## Real Uvicorn HTTP scale probe (1015)

`make scout-http-load-probe` adds network/server/API proof to the existing deterministic projection/store probe. It uses 5,000 repositories, four Steps per repository and 500 concurrent HTTP clients against a real Uvicorn application process. One real verification-purpose Goon is executed first to establish canonical Journal revision identity; only then is the disposable Scout sidecar replaced with deterministic load-test observations at that same revision.

Final 1015 sandbox result:

```text
repositories:                    5,000
Steps/repository:                     4
Step observations:               20,000
concurrent HTTP clients:             500
successful HTTP requests:            500
concurrent wave total:             9.833 s
client latency p50:                1.797 s
client latency p95:                3.101 s
overview request:                  1.079 s
findings request:                  0.356 s
Goon detail request:              0.400 s
Repository detail request:        0.073 s
search request:                   0.021 s
Scout health request:             0.010 s
```

These are local measurements, not an SLO. The gate requires correct responses and complete concurrency rather than asserting an invented universal latency threshold. Reverse-proxy/network/hardware certification remains deployment-specific.


## Abrupt crash/restart persistence certification (1019)

`make scout-crash-restart-certification` runs the real guarded Uvicorn service, creates canonical Journal truth, seeds the disposable Scout sidecar exactly once, warms the analytical routes, and then terminates Uvicorn with SIGKILL. The same canonical DB and sidecar are reopened without reseeding.

Final 1019 sandbox result:

```text
repositories:                       5,000
Step observations:                 20,000
Journal revision:                       4
sidecar bytes after SIGKILL:   17,301,504
sidecar bytes after restart:   17,301,504
retained Runs after restart:        5,000
retained Steps after restart:      20,000
Journal lag after restart:              0
post-restart requests:            500/500
wave total:                        9.951 s
p50:                               1.515 s
p95:                               2.563 s
response fingerprints preserved:      yes
non-loopback outbound attempts:         0
```

This proves abrupt process death does not require Scout reseeding when the state volume itself remains intact. It does not claim storage-device or host-filesystem crash atomicity beyond SQLite's own durability boundary.

## Real Scout failure-isolation certification (1020)

`make scout-failure-isolation-certification` starts the real guarded API with an intentionally unusable Scout analytical path. Scout health must fail first; the same process then executes the real acceptance Goon. The Goon must complete successfully and preserve normal Game Tape evidence while Scout remains failed. This proves the observer is not in the execution/admission dependency chain.

## Real corrupt-sidecar recovery certification (1021)

`make scout-corruption-recovery-certification` replaces only the disposable Scout SQLite sidecar with invalid bytes after a real canonical Goon has committed Journal truth. On restart, Scout must discard/recreate that sidecar from Journal facts without changing canonical durable revision. A second real Goon must still complete with normal Game Tape evidence, and Scout must advance to zero lag afterward.

## Real retention/checkpoint certification (1022)

`make scout-retention-checkpoint-certification` proves the canonical Scout retention policy through the real HTTP service rather than direct pruning calls. A fresh observation, a 120-day observation and a 400-day observation are seeded permissively into the disposable sidecar at the real Journal watermark. Service maintenance must retain 2 Runs (fresh + 120-day) but only 1 Step detail observation (fresh), leave canonical Journal revision unchanged, keep zero lag and truncate the Scout WAL.
