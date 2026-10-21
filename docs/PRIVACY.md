# Privacy and fully local operation

oh-goon is a fully local developer control plane. React, FastAPI, Git, uv, pytest, repository discovery, and state projection all execute on the developer machine.

## Browser storage

Harmless presentation preferences may persist through the dedicated `frontend/src/app/preferences.ts` boundary. Today this includes the selected language.

The browser must not become a second source of truth for repository/business state. Do not persist repository locations, exclusions, readiness, operation reports, fleet runs/progress, Git/uv state, or source contents in localStorage, sessionStorage, IndexedDB, cookies, or another browser database.

## Durable backend state

Retained Plan configuration lineage stores only bounded structured decision
metadata: names, occurrence/parent references, source digests/marks, types,
value states and source-selection outcomes. It does not copy input/default/env
values or source snippets. It follows the canonical target/Journal lifecycle,
without a second report database, automatic report files or browser storage.

The folders a user chooses for the local League are a small configuration preference stored in `league.yml` outside disposable run history. It contains only versioned local folder paths; Teams and `goon.yml` state are rediscovered from disk and are never persisted as a shadow catalog.

oh-goon persists canonical operational/runtime state in one SQLite database:

- a runtime projection of the saved League folders for transactionally consistent Fleet admission;
- repository exclusion policy (canonical path, reason, timestamp);
- immutable `OperationReport` journal facts;
- immutable fleet lifecycle events;
- repository Schedule definitions plus internal schedule-execution bookkeeping facts;
- one renewable execution-lease row containing local coordination metadata such as owner PID, heartbeat timestamp, run ID, and cancellation request timestamp while work is active.

The optional Scout observer keeps only rebuildable analytical metadata/resource observations in an adjacent SQLite sidecar (for example `state.scout.sqlite3`). The sidecar is created only when Scout is enabled, contains no repository source, stdout/stderr, command output, secret/environment values, leases, Journal entries or other execution authority, and may be deleted/rebuilt without changing execution. Because it is a separate SQLite file, Scout analytical writes cannot hold the canonical execution database writer lock.

The default database lives in the operating system's local application-data area. Set `OH_GOON_DB` to override the path. The default League preference lives in the operating system's local configuration area; set `OH_GOON_LEAGUE_FILE` to override it. If `OH_GOON_DB` is explicitly overridden without a League-file override, `league.yml` is kept beside that database. The database and preference file contain metadata/paths, not repository source-file contents.

`LocalState.read_view()` reads locations, exclusions, and the append-only journal in one SQLite read transaction. A published `FleetSnapshot` therefore represents one consistent database cut rather than a mixture of independently timed reads. Execution-lease heartbeats are coordination traffic rather than application facts, so they deliberately do not advance `durable_revision` or change snapshot identity.

On restart, history persists and the League-folder runtime projection is restored from `league.yml`. A disposable database reset therefore does not erase the user's League. Results produced by an earlier application session project as `STALE` until revalidated in the current session. A still-live execution owner is respected rather than "recovered" by another backend process. Once the previous lease is genuinely abandoned, an interrupted cancellation is finalized as `CANCELLED`; another abandoned non-terminal run is recorded as failed instead of leaving a permanent active-run lock.

UI-triggered engine execution still disables persistent engine report/history output. The engine writes its structured report to an OS temporary directory, FastAPI validates and commits the canonical report to SQLite, and the temporary directory is deleted.

## Local access and network boundaries

The supported server command binds only to loopback (`127.0.0.1`, `localhost`, or `::1`), but loopback is **not** treated as authorization: another operating-system user on the same host can otherwise reach a loopback TCP port. Every domain `/api/*` request therefore requires one process-local 256-bit access capability. `oh-goon start`, `oh-goon serve`, `make dev`, container startup and runtime certification each create or explicitly receive that capability through one transport owner. `/healthz` is the only unauthenticated liveness endpoint and contains no repository, Run, Plan, Schedule, Scout or filesystem data.

The normal browser launch URL carries the capability only in the URL fragment (`#access=...`), which is not sent in the HTTP request. The frontend exchanges it once through `/api/local-session` for an HttpOnly `SameSite=Strict` cookie scoped to `/api`, then removes the fragment from browser history before normal workflows render. API/automation clients use `X-Oh-Goon-Local-Access`. The capability is transport authorization only: it does not create Users, Tenants, Roles, repository ACLs or a second product identity model. IDs such as Run IDs, repository IDs, Schedule IDs and retained-content refs remain identifiers, never credentials; backend services must still prove parent/repository ownership before using them. Rerun/readmission derive authority from the durable Run identified by the parent ID and require a fresh unused child Run ID; Schedule occurrences are bound to their Schedule; retained content is bound to its repository owner. The process-local access capability is also explicitly non-exportable to command subprocesses and Plan host-environment imports.

On POSIX hosts, the canonical operational-data root, League configuration root, saved Plan/Lineup roots, admission-lock directory, and per-user exact-file lock directory are actively re-hardened to owner-only directories (`0700`) even when the directory already existed. The operational SQLite database, Scout sidecars, League preference file, saved Plan/Lineup files, and lock files are owner-only (`0600`). On Windows, default state lives under the current user profile and inherits that profile's ACL; custom override paths must retain equivalent user-private ACLs.

FastAPI also adds same-origin Content Security Policy and trusted-host restrictions. The React application talks only to the local `/api` boundary. oh-goon does not add telemetry, analytics, or a source-code upload service.
Opt-in watch notifications use the browser/operating-system Notification API only after the user grants permission. Notification text is generated locally from Schedule health-change projections; oh-goon does not send notification content to a remote service.

Dependency maintenance itself can require network access: Git can contact configured remotes, uv can contact configured package indexes, and security-audit operations can contact their configured advisory service.

## Enforcement

The repository quality gates enforce the local-app/SSOT contract through frontend architecture checks and the backend architecture owner. `make check` exercises the fast development architecture projection; stricter frontend/backend quality owners run in their dedicated checks and certification. Browser business-state persistence, telemetry beacons, external frontend URLs, unapproved backend persistence stores, and direct SQLite access outside the canonical or explicitly allowed Scout analytical persistence boundaries fail the quality gate. Direct localStorage access is allowed only through `frontend/src/app/preferences.ts` for harmless presentation preferences.
