# The Scout — production certification status

This document records the production-proof work after the first complete Scout implementation.
It is deliberately narrower than `THE_SCOUT.md`: it says what has been proved, what is environment-limited, and what must wait for a generic Oh Goon owner instead of becoming Scout-specific architecture.


## 977 privacy/deployment proof

The 977 release strengthens the sidecar as a privacy-safe disposable observer boundary rather than adding Scout product features.

- analytical read transactions are SQLite `query_only`;
- the database/WAL/SHM family is owner-only on POSIX and re-hardened after restart, with race-safe handling when SQLite removes transient WAL/SHM files during concurrent reads;
- symlink and non-regular sidecar-family paths are refused before SQLite opens them;
- trusted schema execution is disabled and analytical deletes use SQLite secure-delete;
- hourly retention checkpoints/truncates the Scout WAL so deleted metadata does not linger indefinitely in the analytical log;
- genuine corrupt/non-database Scout sidecars are discarded and recreated from empty derived state, leaving canonical Journal state untouched;
- Scout health exposes analytical sidecar storage bytes for deployment growth monitoring.

Corruption recovery is intentionally asymmetric: canonical Oh Goon state still fails according to its own durable-state rules, while Scout may rebuild because its sidecar is never authoritative.

## 976 runtime-hardening proof

The 976 release strengthens the execution-independence proof under an active league rather than adding new Scout product features.

- unchanged Journal revisions return before canonical Run-detail reconstruction;
- decoded analytical windows refresh exactly by changed Run identity instead of being discarded after every revision;
- the active-league probe measures repeated changed-repository revisions in addition to stable cached reads;
- derived observations live in the optional adjacent `*.scout.sqlite3` sidecar, never in canonical execution SQLite tables;
- a regression holds the canonical database write lock while Scout materializes successfully, proving Scout cannot delay execution through SQLite writer contention;
- disabled Scout creates no sidecar;
- the sidecar processed revision is monotonic across stale/concurrent workers.

The sidecar remains an observer cache, not a second source of execution truth. It can be deleted and rebuilt from the Journal without changing a Run.

## Certified in-repository

### Browser acceptance contract

`frontend/e2e/scout.spec.ts` defines the release journeys for:

- simple overview → Heavy Hitter → Repository → Step → Game Tape → browser Back;
- CPU-outlier evidence details and comparable execution/resource environment;
- paginated Goon Leaders → Goon drill-down;
- Repository/Plan/Goon search destinations;
- URL-durable custom analytical windows.

The slim execution environment cannot install `@playwright/test`, React, Vite or the remaining pinned npm dependency tree, so these Playwright journeys are checked in but require the normal frontend dependency installation before they can execute. This is an environment limitation, not a reason to replace browser tests with weaker Scout-specific test infrastructure.

### 5,000-repository / 500-user local load probe

Run:

```text
make scout-load-probe
```

The deterministic probe generates 5,000 repositories, four Step observations per repository, projects overview/findings, materializes the derived sidecar, executes 500 concurrent cached reads, then applies repeated active Journal revision cycles that change a bounded repository population. It reports stable-read latency together with active-revision refresh p50/p95 timings.

The probe is not a production SLO and does not simulate network latency, reverse proxies or real storage hardware. It exists so deployments can repeat the same stable and active-league workload and compare their own p50/p95 numbers instead of inventing a performance claim.

### 5,000-repository / 500-user real HTTP probe

Run:

```text
make scout-http-load-probe
```

This is deliberately separate from the in-process projection/cache probe. It starts a real Uvicorn Oh Goon API, executes one normal repository Goon first so canonical Journal truth exists, stops the server, seeds only the disposable Scout analytical sidecar with the same deterministic 5,000-repository / four-Step dataset at that exact Journal revision, restarts the real API, checks representative Scout health/overview/findings/Goon Leaders/Repository/Plan/Goon/search routes, and then sends a 500-client concurrent HTTP wave. Direct sidecar seeding is load-test setup only; it is not claimed as executor-ingestion proof.

The final 1015 sandbox measurement completed **500/500 HTTP requests** with 20,000 Step observations. The concurrent wave completed in **9.833 s**, with **1.797 s p50** and **3.101 s p95** observed client latency. Individual cold representative requests measured approximately: Scout health 9.9 ms, search 20.7 ms, Goon Leaders 24.2 ms, Repository 72.7 ms, Plan 181.8 ms, findings 355.8 ms, Goon 399.6 ms, and overview 1,079.2 ms. These numbers describe this sandbox only and are not production SLOs.

The 500-client wave is intentionally operator-shaped rather than 500 duplicate overview projections: 80% health reads, 14% search, and 6% rotating analytical/drill-down reads. Every major analytical route is measured separately before the concurrent wave. A deployment should repeat the command through its real reverse proxy/hardware before declaring deployment-specific latency targets.

### Concurrent read invariant

The backend suite includes a 500-concurrent-reader regression proving that one stable Journal revision shares one materialized Scout snapshot without SQLite/threading failures.

### Privacy/storage invariant

Scout analytical tables have an explicit metadata/resource column allowlist. Tests reject accidental persistence of log, command, source-code, token/password/secret or environment-value fields.

Scout remains downstream of canonical evidence and never stores stdout/stderr payloads.

### Process-level Scout workload overhead

1018 adds `make scout-overhead-certification` for Linux deployment-equivalent runs. It warms every representative Scout route, samples the real guarded Uvicorn child process from `/proc`, runs the 500-client Scout wave, samples the same process again, and reports process CPU, RSS/peak RSS, Scout sidecar size, cache facts and Journal lag. These are **Oh Goon process measurements while serving Scout traffic**; they are not presented as perfectly isolated Scout-only CPU/memory attribution.

The final 1018 sandbox run over 5,000 repositories / 20,000 Step observations completed **500/500 requests** in **9.717 s**, with **1.651 s p50** and **3.013 s p95**. The warmed wave consumed **11.1 process CPU-seconds**. RSS moved from approximately **235.1 MiB** before the wave to **350.9 MiB** after it, with **374.2 MiB peak RSS**. The analytical sidecar occupied **16.5 MiB**, Journal lag remained **0 revisions**, the snapshot cache reported **35 hits / 1 miss**, and the guarded process made **zero non-loopback attempts**.

These measurements are a repeatable capacity reference for this sandbox, not a universal memory/CPU budget. Deployment owners should run the same command under their real CPU limits, filesystem and process/container constraints before setting capacity thresholds.

### Real Nginx reverse-proxy path

1017 adds `make scout-proxy-certification`. It starts the guarded real Oh Goon/Uvicorn process on one loopback port and a real Nginx process on a second loopback port. The acceptance Goon, representative Scout reads and the 500-client wave all use the Nginx URL; clients never address Uvicorn directly during the measured stage. The generated test configuration disables request/response proxy buffering and forwards the normal Host / X-Forwarded-* headers.

The final post-fix 1017 sandbox run used Nginx 1.26.3 and completed **500/500 proxied requests** over 5,000 repositories / 20,000 Step observations. The concurrent wave completed in **9.955 s**, with **1.688 s p50** and **3.182 s p95** measured latency. Representative proxied requests measured approximately: health 5.7 ms, search 21.4 ms, Goon Leaders 26.8 ms, Repository 76.7 ms, Plan 184.6 ms, findings 321.9 ms, Goon 512.6 ms and overview 848.9 ms. The guarded Oh Goon process recorded **zero non-loopback outbound attempts**.

This proves the application/proxy path and concurrency behavior with a real reverse proxy. It is still not a production latency SLO: deployments should repeat the certification through their actual ingress/reverse-proxy configuration and storage/hardware.

The proxy stress also exposed a real transient SQLite race in Scout health: `storage_bytes()` previously checked WAL/SHM existence and then called `stat()`, allowing SQLite to remove `*.db-shm` between those operations and produce an intermittent HTTP 500. 1017 fixes storage accounting by opening each sidecar-family member with the same no-follow regular-file boundary used by permission hardening, using `fstat()` on the opened descriptor and treating `FileNotFoundError` as a normal concurrent WAL/SHM disappearance.





### Real retention and WAL checkpointing

1022 adds `make scout-retention-checkpoint-certification`. It creates real canonical Journal truth, then seeds only the disposable Scout sidecar with three synthetic age bands under the same Journal revision: a fresh Run/Step, a 120-day-old Run/Step, and a 400-day-old Run/Step. The seed uses deliberately permissive 1,000-day retention only as setup so the real Scout service—not the fixture—must enforce canonical policy.

On real HTTP Scout health access, the service applies the production retention boundary: Run observations retain 365 days while Step detail/resource observations retain 90 days. The expected post-maintenance state is therefore **2 retained Runs / 1 retained Step**. The certification also requires canonical Journal revision to remain unchanged, Scout lag to stay zero, the analytical WAL to be absent or 0 bytes after checkpoint/truncation, and the guarded server to make zero non-loopback connections.

### Real corrupt-sidecar recovery

1021 adds `make scout-corruption-recovery-certification`. It executes a real verification Goon first so canonical Journal truth exists, stops the server, replaces only the disposable Scout sidecar with invalid SQLite bytes, then restarts the guarded real API and requests Scout health. The normal Scout store initialization must discard/recreate the corrupt sidecar and repopulate it from canonical Journal facts.

The certification requires canonical Journal revision to remain unchanged across recovery, Scout processed revision to catch up exactly with zero lag, the real Run observation to reappear, and a second real Goon to complete successfully with `acceptance-step` still present in Game Tape. The acceptance command has no Scout CPU/memory telemetry, so recovery correctly retains Run observations while Step resource observations remain zero. This keeps the ownership distinction explicit: Game Tape proves Step execution; Scout stores only supported resource metadata.

### Real Scout failure isolation

1020 adds `make scout-failure-isolation-certification`. It intentionally makes the Scout analytical sidecar unusable by placing a directory at the expected `*.scout.db` path, starts the real guarded Uvicorn application, and first proves `/api/scout/health` is genuinely failing. Without changing or repairing Scout, the same running process then executes the normal verification-purpose acceptance Goon through the public API.

The certification passes only when the Fleet Run reaches **COMPLETED / SUCCESS**, Game Tape contains the real `acceptance-step`, Scout is still failing afterward, and the guarded server records zero non-loopback outbound attempts. This is direct runtime evidence for the architectural stopping rule: Scout availability is not an admission/execution prerequisite and Scout failure cannot change whether a Plan/Goon executes.

### Abrupt crash/restart persistence

1019 adds `make scout-crash-restart-certification`. It establishes canonical Journal truth, seeds the disposable Scout sidecar once with the deterministic 5,000-repository / 20,000-Step dataset, warms representative analytical routes/cache activity, then kills the guarded real Uvicorn process with **SIGKILL**. The certification restarts Oh Goon against the exact same canonical database and Scout sidecar without reseeding either one.

The restart must preserve retained Run/Step populations, the processed Journal revision, zero Journal lag and stable analytical response fingerprints before a second 500-client HTTP wave is allowed to run. The sidecar must remain present across the abrupt crash, and the guarded process must still make zero non-loopback outbound connections.

Final 1019 sandbox measurement: **5,000 repositories**, **20,000 Step observations**, Journal revision **4**, sidecar **17,301,504 bytes** immediately after SIGKILL and after restart, **500/500** successful post-restart requests, **9.951 s** total wave, **1.515 s p50**, **2.563 s p95**, fingerprints preserved, **0 revision lag**, and **0 outbound attempts**. These are repeatable sandbox measurements, not a universal recovery SLO.

### GitLab / outbound-network independence

`OBS-001` rejects GitLab coupling inside Scout-owned backend modules. Scout observes Oh Goon Journal/Game Tape facts; it never scans GitLab pipelines/jobs/logs to reconstruct history.

1016 adds runtime proof with `make scout-network-certification`. The real Uvicorn process installs an explicit socket guard before the application is imported: loopback TCP and Unix-domain sockets remain available, while any non-loopback `connect` / `connect_ex` is blocked and appended to an audit log. The certification fails if that audit contains even one destination, including a call that application code catches internally.

The final 1016 sandbox run used the same 5,000-repository / 20,000-Step / 500-client HTTP workload and completed **500/500 requests** with **zero non-loopback connection attempts**. The concurrent wave completed in **10.339 s**, with **1.792 s p50** and **2.964 s p95** measured latency. Representative guarded requests measured approximately: health 5.3 ms, search 21.3 ms, Goon Leaders 27.7 ms, Repository 74.3 ms, Plan 229.2 ms, findings 333.6 ms, Goon 380.2 ms, and overview 847.8 ms. These remain sandbox measurements, not production SLOs.

The normal one-way GitLab migration capability elsewhere in Oh Goon is unrelated and may continue to use GitLab during explicit import. The runtime certification is scoped to the Oh Goon/Scout API process used for the browsing/load journey; it does not forbid unrelated administrator tooling from using the network.

## Multi-user authorization — intentionally not Scout-owned

The current Oh Goon API is a local-only control plane protected by one process-local access capability. It still has no generic multi-user User/Tenant/repository-visibility owner. Therefore Scout must **not** invent `ScoutUser`, Scout RBAC, Scout repository ACLs, or a parallel identity model; the local-access capability is transport authorization, not repository visibility policy.

When Oh Goon introduces a generic server-side authorization boundary, Scout must consume that owner so:

- repository users see only repositories already permitted by Oh Goon;
- team/group visibility follows the same canonical repository ownership;
- platform/operator roles may receive league-wide Scout views where centrally permitted;
- every Scout aggregation/search/ranking is filtered before derived evidence is returned.

Until that generic owner exists, Scout is certified only under Oh Goon's current local-control-plane trust model. This is a stopping rule, not missing Scout-specific code.

## Production deployment completion

A server deployment should run these before declaring Scout production-proven:

1. install the exact frontend dependencies and browsers;
2. run the Scout Playwright acceptance journeys plus accessibility/visibility/visual suites;
3. run `make scout-load-probe` on deployment-equivalent hardware;
4. repeat `make scout-http-load-probe` through the deployment reverse proxy/hardware and compare its measured latency;
5. run `make scout-network-certification` in the deployed runtime namespace and confirm zero non-loopback attempts remain true with deployment instrumentation enabled;
6. verify the central authorization owner, once available, filters every Scout read surface;
7. run `make scout-crash-restart-certification` and prove the deployed state volume survives abrupt process death without reseeding;
8. review query latency, Journal revision lag, retained observation counts, cache hit/miss behavior and snapshot-load latency under real usage.

Further Scout features should then be driven by those observations, not by adding metrics or dashboards speculatively.
