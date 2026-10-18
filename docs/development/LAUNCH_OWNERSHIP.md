# Local Launch Ownership Inventory

This document began as the Phase 0 local-launch ownership inventory. The opening inventory below is **historical baseline evidence**, not the current runtime contract. Current source has since landed the Launch Manifest/runtime bridge, deterministic current-launch binding, process-birth identity, attach/reuse admission, observed-endpoint navigation, structured launch logs, and birth-proven stop control. Later sections record those landed closures. The [repository README](../../README.md) describes the development checkout. Archive-local `CHECKPOINT_NOT_RELEASE.md` records belong to their original checkpoints and are not required in a GitHub clone.

## Historical Phase 0 entry paths

### Contributor development

```text
make dev
  -> Make prerequisite checks / static directory preparation
  -> scripts/dev.sh
       -> local-state prelaunch-check
       -> local access capability creation
       -> backend uvicorn --reload child
       -> frontend npm/Vite child
       -> shell job/trap shutdown
```

### Built local release

```text
oh-goon start
  -> local-state prelaunch-check
  -> built-UI availability / League preference checks
  -> local access capability creation
  -> canonical local URL rendering
  -> uvicorn.run(...)
       -> FastAPI lifespan
            -> ApplicationRuntime.start_background()
            -> ApplicationRuntime.shutdown()
```

The two paths **at Phase 0** converged only at the backend ASGI application. This sentence describes the historical baseline; use the landed runtime-evidence sections below and current source for present authority.

`local-state check` remains a read-only schema/integrity diagnostic. Launch paths use the stronger `local-state prelaunch-check`: an existing database must pass schema/integrity plus a non-semantic `BEGIN IMMEDIATE`/rollback write-lock proof, while a missing database is left absent and its parent directory is proven with a disposable sibling SQLite create/commit/reopen/integrity round-trip. This happens before local access capability creation, uvicorn, or Vite.

## Historical Phase 0 responsibility map

> **Historical snapshot:** every `Current`, `Target consequence`, `ownership gap`, and `later phase`
> statement in this table describes the Phase 0 baseline only. The landed sections below supersede
> those status claims; do not turn them into present-day work without reconciling current source.

| Responsibility | Phase 0 writer/owner | Phase 0 classification | Historical target consequence |
| --- | --- | --- | --- |
| `make dev` prerequisite ordering | `Makefile` | delegating caller | Keep Make boring; prerequisites may remain but process orchestration must leave Make/shell. |
| Contributor backend process creation | `scripts/dev.sh` | canonical owner for contributor mode | Duplicate launch ownership; migrate behind the canonical Oh-Goon launch owner in a later phase. |
| Contributor frontend process creation | `scripts/dev.sh`, delegating to `npm run dev` / Vite | orchestration owner + native runtime owner | Oh-Goon may orchestrate; npm/Vite retain compilation/HMR ownership. |
| Contributor process-tree shutdown | `scripts/dev.sh` shell jobs/trap | canonical owner for contributor mode | Must later converge with one Oh-Goon launch lifecycle owner; no silent ownership transfer. |
| Built-release server process | `oh_goon.cli_start.run_start_command`, delegating to uvicorn | canonical owner for built mode | Candidate integration point for the future canonical launch owner; uvicorn remains native server owner. |
| Application background lifecycle | FastAPI lifespan -> `ApplicationRuntime` | canonical application-runtime owner | Remains below launch/process ownership; does not become the process manager. |
| Local access capability, contributor | `scripts/dev.sh` via `oh-goon-local-access-token` | canonical owner for contributor mode | Duplicate capability issuance path to remove after a shared launch owner exists. |
| Local access capability, built | `cli_start.py` via `ensure_local_access_token()` | canonical owner for built mode | Must be issued once by the shared launch owner; manifest may project metadata but never store the secret. |
| API access enforcement | `LocalAccessMiddleware` / exchange endpoint | canonical enforcement owner | Remains enforcement owner; Launch Manifest is read-only projection. |
| Contributor primary URL | `scripts/dev.sh` hard-coded Vite URL | duplicate/legacy truth | Must later derive from canonical launch state, not be reconstructed in a renderer. |
| Built primary URL | `cli_start.py` from local host/port + access capability | canonical truth for built mode | Must later come from resolved intent + observed launch state. |
| Backend bind intent | `scripts/dev.sh` uvicorn args and `cli_start.py` constants | duplicate configuration writers | Resolve once upstream; configured port never proves observed binding. |
| Frontend bind intent | Vite config / npm command | native frontend configuration owner | Oh-Goon observes/records; does not reimplement Vite. |
| Backend readiness | no canonical structured local-launch fact | ownership gap | Add the cheapest authoritative readiness signal in a later phase; process existence is insufficient. |
| Frontend readiness | no canonical Oh-Goon structured fact | ownership gap | Observe a native signal; do not parse arbitrary logs. |
| Continuous health | `/healthz` exists for backend; launch does not own continuous health state | observer capability, not launch truth | Keep optional/unknown unless a real launch contract requires it. |
| Runtime logs | native uvicorn/Vite stdout/stderr | evidence owners | Logs remain evidence, never canonical lifecycle state. |
| Launch identity | none shared across contributor/built launch | ownership gap | Introduce exactly one canonical `launch_id` per launch. |
| Owner/service generation | none | ownership gap | Required before attach/reuse/restart semantics so PID reuse/stale events cannot become current truth. |
| Current-launch selection | none | ownership gap | Must later be workspace + owner-generation bound, never “newest manifest wins.” |

## Non-negotiable boundaries discovered by the Phase 0 inventory

1. `scripts/dev.sh` and `cli_start.py` are **not** allowed to become two Launch Manifest writers. The manifest must be downstream of one eventual launch owner.
2. FastAPI/ApplicationRuntime owns application background lifecycle, not OS process-tree lifecycle.
3. uvicorn owns server mechanics; Vite owns HMR/compilation; npm owns Node command execution. Launch Manifest observes these owners.
4. The configured `127.0.0.1:8787` or `127.0.0.1:5173` values are intent only. They do not prove a socket is bound or a service is ready.
5. Access tokens are capabilities. A safe/persistent Launch Manifest may state that a launch-session credential exists, but must not contain the raw token.
6. No current log string is promoted to canonical readiness or terminal state.
7. Phase 0 intentionally leaves behavior unchanged. Duplicate owners are documented for removal only after the replacement owner is proven.

## Phase 0 exit result

**Historical Phase 0 result:** every responsibility visible at that checkpoint had either an identified owner or an explicit ownership gap. The then-next phase was allowed to introduce the Launch Manifest domain model only as an output/read model, not launch configuration or process authority. The subsequent landed sections below supersede this historical next-step statement.

## 1267.0.37 runtime-evidence bridge

The first runtime integration now exists, but it deliberately does **not** pretend the contributor and built launch paths have already converged into one process owner.

### Contributor `make dev`

`scripts/dev.sh` remains the contributor orchestration owner for this bridge checkpoint. Before native uvicorn or npm/Vite takes over a child PID, the `oh-goon-local-launch-exec` bridge records `service.spawned` and then uses `exec` so the recorded PID is the same process identity root handed to the native runtime. This closes the startup race where FastAPI could previously observe startup before the launch owner had durable spawn evidence.

The backend lifespan records only `service.startup_complete`; it does not claim socket binding or readiness. `oh-goon-local-dev-launch observe-ready` then performs bounded HTTP observations against backend `/healthz` and the native Vite endpoint. Only successful HTTP responses can produce `service.bound`, `service.ready`, and finally `launch.ready`. Vite/npm and uvicorn still own their native runtime behavior; Oh-Goon observes rather than parses their logs.

### Built `oh-goon start`

`cli_start.py` creates the same structured launch identity/event stream, records the current process as the backend service root, propagates generation-bound launch context into the FastAPI lifespan, and still calls native `uvicorn.run(...)`. A small read-only readiness observer probes `/healthz`; configured host/port and `uvicorn.run` invocation are not accepted as readiness evidence.

### Terminal semantics in this bridge

- A deliberate stop after `launch.ready` becomes `launch.stopping -> launch.stopped`.
- A stop before readiness becomes `launch.aborted`; absence of a process is never used to infer terminal state.
- A required contributor child exit is recorded as structured `service.failed` plus `launch.failed` before the sibling process is cleaned up.
- A readiness timeout is a structured `readiness/readiness_timeout` failure, not a guessed process failure and not a log-parsing result.
- A terminal owner transition wins over a late readiness observer; observers cannot overwrite terminal truth.

**Historical 1267.0.37 status:** this was still a bridge at that checkpoint. Current-launch selection, attach/reuse compatibility and process-birth verification listed here subsequently landed in the sections below. Treat any remaining item as open only after reconciling current source.

## 1267.0.38 deterministic current-launch binding and status

Current-launch selection is now explicit rather than an ownership gap. The canonical workspace path is hashed into one exact binding filename under application state; status reads that one binding, then the one referenced launch record. It does not enumerate `local-launches`, compare mtimes, or choose a recent-looking launch.

The binding carries the exact `launch_id`, owner generation, and resolved launch-intent identity. A new launch may replace an existing workspace binding only when the exact previously bound launch proves a terminal lifecycle from its durable owner-issued event stream. A missing/corrupt record, generation mismatch, intent mismatch, or any nonterminal lifecycle is a conflict and blocks replacement. This is intentionally conservative until process birth identity makes live-owner proof possible.

`oh-goon status` is a presentation reader only. `oh-goon status --json` emits one `oh-goon.launch-status/v1` document built by a centralized safe whitelist. It does not serialize raw Launch Manifest/event bytes and does not expose launch commands or the local-access capability. Until process birth identity is added, nonterminal launches retain their last authoritative lifecycle while reporting `Freshness: stale` and `Owner proof: unavailable`; owner uncertainty is not rewritten into STOPPED/FAILED.

## 1267.0.39 — process birth identity

A nonterminal launch may report `owner_proof=available` only when the exact recorded owner
process instance can be re-observed. PID presence is insufficient because operating systems
reuse PIDs. On Linux the proof identity is `pid + /proc boot_id + /proc/<pid>/stat starttime`.
The boot ID distinguishes reboots and start ticks distinguish a reused PID within one boot.
Unsupported or unreadable procfs produces unknown owner proof; it never implies STOPPED or
FAILED.

Owner process identity and service process identity are separate observations. Contributor
`make dev` records the actual `scripts/dev.sh` shell PID as launch owner before children start.
Each `oh-goon-local-launch-exec` bridge separately records its own child birth identity before
`exec`, and that identity deliberately survives transfer to uvicorn/npm because `exec` retains
the process instance. Built `oh-goon start` records its current Python process as both launch
owner and backend service process where appropriate.

The event stream remains backward-readable: earlier `service.spawned` v1 records containing
only a PID can still be projected, but they cannot establish birth proof. New launch-owner
evidence requires a structured birth identity and must precede service process observations.
Status compares the recorded owner birth identity with a fresh observation. Match means
`current/available`; missing or mismatched birth means `stale/unavailable` while preserving the
last authoritative lifecycle. This phase does not authorize automatic attach, replacement,
kill, restart, or stale-owner repair.

## 1267.0.40 — identity-bound attach/reuse admission

Repeated local start is now an identity decision before it is an execution decision. The
reuse assessor reads exactly the canonical workspace binding and its exact launch record. It
returns only `start_new`, `reuse`, or `conflict`; it cannot spawn, stop, kill, repair, rebind,
or replace anything.

A nonterminal launch is reusable only when the requested resolved launch-intent identity and
profile exactly match the bound launch, the binding still matches the launch workspace and
owner generation, and the recorded launch-owner birth identity can be positively re-observed.
A PID match alone is insufficient. A missing/malformed bound record, mismatched intent, reused
PID, or unavailable owner birth proof is an explicit conflict and never authorizes a second
owner. A terminal bound launch permits the normal binding owner to create the next launch.

Both built `oh-goon start` and contributor `make dev` resolve this decision before unrelated
startup work. A compatible rerun returns the existing launch without invoking uvicorn/npm/Vite
or installing another cleanup owner. A conflicting rerun explains the current launch and
points to `oh-goon status`. Token/UI/League-folder work and native child execution remain only
on the paths that actually need them. This phase deliberately does not infer source/config
drift; that remains separate Launch Manifest work rather than being guessed from process state.

## 1267.0.42 — observed-endpoint navigation and private capability handoff

`oh-goon open` is a thin presentation/action consumer of the exact current Launch Manifest. It resolves the canonical workspace binding and exact bound launch, revalidates binding identity, requires positive launch-owner birth proof, and opens only an authoritative ready UI service whose **observed** binding is loopback. Configured endpoint intent never supplies the browser host or port.

The browser bootstrap capability remains outside Launch Manifest. The launch owner publishes the exact token it actually exports to the backend into `LocalLaunchAccessStore`, an owner-private exact-launch/generation-bound store beneath application state. The store is not lifecycle truth, a launch registry, or discovery authority; `oh-goon open` cannot mint a replacement capability for an already-running launch. Public status, events, Launch Manifest serialization, and normal `open` output remain credential-free.

Profile-to-navigation-service policy is explicit and small: built-local uses the backend UI endpoint; contributor-dev uses the frontend/Vite endpoint. Unsupported profiles fail closed instead of guessing which ready service is browser-facing. Browser invocation is delegated to the platform browser opener; Oh-Goon does not manage browser processes.

## 1267.0.951 — local startup authority convergence

Contributor startup no longer uses `scripts/dev.sh` as a process/readiness owner. `make dev`
performs prerequisite work before runtime startup, then enters the Python local-dev frontdoor.
`LocalLaunchPlan` is the single resolved source for exact service argv, cwd, endpoint intent,
expected readiness status, and the fixed 30-second startup budget. `LocalLaunchSupervisor` owns
contributor process creation, required-service liveness, readiness observations, launch-ready
transition, and process-group shutdown. `scripts/dev.sh` is only a thin `exec` frontdoor.

The startup budget begins when physical runtime startup begins; prerequisite/dependency checks do
not consume it. There is no nested backend-then-frontend timeout. Every required contributor
service shares one absolute 30-second budget, and a required child exit races readiness so the
first causal failure wins. HTTP probes are single observations; they do not own retry deadlines.

Built `oh-goon start` now derives its `LaunchIntent` from `built_local_plan()` and uses the same
supervisor-owned readiness coordinator and readiness policy. The built server remains in-process
because uvicorn owns its native server mechanics, but `cli_start.py` no longer implements a second
HTTP polling/readiness algorithm. It creates a native `uvicorn.Server`; readiness timeout records
the structured launch failure and requests that native server to exit. The access URL is published
only by the successful readiness transition, never merely because configured host/port values
exist.

`LocalLaunchOwner` remains a durable event/fact writer. It does not probe HTTP, own a timeout,
spawn contributor services, or supervise native runtimes. The `oh-goon-local-launch-exec` bridge
remains the pre-exec process-evidence boundary so the recorded child PID is the same process root
transferred to uvicorn/npm/Vite.

There is exactly one readiness policy for contributor and built-local profiles: plan-derived
endpoint + exact expected status, one supervisor-owned startup budget, structured terminal
failure, and terminal-owner transitions winning over late observations. stdout/stderr is evidence
only and is never a machine lifecycle protocol.


## 1267.0.952 spawn-registration authority closure

Contributor startup is now owned by `LocalLaunchSupervisor`; `scripts/dev.sh` is only a thin frontdoor. The `oh-goon-local-launch-exec` bridge must durably append generation-bound `service.spawned`, acknowledge that commit over a private inherited pipe, and only then `exec` the native uvicorn/npm runtime. The supervisor cannot perform liveness or readiness observation for that child before the acknowledgement. If the bridge exits or closes the acknowledgement channel first, startup fails at launch scope because no service runtime event is valid without prior spawn evidence.

The single 30-second startup budget starts at the first physical child launch and includes spawn registration plus readiness. Registration does not receive a second timeout. Once registration is acknowledged, an immediate native child exit is a valid `service.failed(child_exit)` followed by `launch.failed`. If registration itself cannot be established, cleanup owns the physical process group and no fabricated `service.failed` event is emitted.

## 1267.0.956 — runtime authority duplication closure

The repository now carries a machine-checked inventory of every production Python module that
physically creates a long-lived process, signals a process group, or instantiates the native
uvicorn server. The inventory lives in `RUNTIME_AUTHORITY_REGISTRY.json`; adding a new physical
lifecycle owner without classifying its semantic authority domain is an architecture violation.

This sweep deliberately does **not** funnel every `Popen` through `LocalLaunchSupervisor`.
Local contributor launch, admitted Step commands, repository workflow frontdoors, OpenSandbox
attempt isolation, native built uvicorn, Scout qualification, release qualification, and product
acceptance are distinct authority domains. Sharing mechanics across those domains would create a
more dangerous parallel semantic owner hidden behind a common helper. Conversely, a second
local-launch process/readiness owner remains forbidden: contributor local startup belongs to
`LocalLaunchSupervisor`, while built uvicorn delegates server mechanics to native uvicorn and
uses the shared local-launch plan/evidence model.

Short bounded `subprocess.run` invocations and individual HTTP observations are not classified as
process-lifecycle owners merely because they execute a command or probe an endpoint. They remain
subject to their existing execution, egress, certification, and readiness contracts. The registry
therefore tracks authority rather than performing a misleading textual ban on process APIs.
