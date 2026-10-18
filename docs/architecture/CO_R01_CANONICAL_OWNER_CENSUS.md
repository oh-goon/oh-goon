# CO-R0 — Source-backed owner and lifecycle census (DB6 → CO-R01)

Parent SHA-256 `9e988b7e9f398cc9a2d3db40991f29456f74ab19f8fed81ea00011caee15f35d`; **reviewed in code, no runtime behavior changed**. Paths below are relative to `backend/src/oh_goon/`; line anchors refer to reviewed DB6 and may move after edits. Disposition `KEEP` means no additional architecture warranted, **not** native/security certification.

| Ingress/operation and concrete edge | Semantic owner / mutation and durable cut | Physical effect / journal / recovery | Disposition and outstanding cut |
|---|---|---|---|
| REST start `api/routes/fleet_runs.py:138` → `control_plane.start` | `services/fleet_run_control_plane.py:126,236`; original `FleetRunLifecycle.admit` and `LocalDatabase` | Fleet `RUN_ADMITTED` journal; same existing executor/settlement | KEEP; original X0 protects REST edge |
| CLI Plan `cli_runtime_plans.py:33` → `services/fleet_plans.py:289` | `FleetPlanService` resolves typed intent; original Fleet control plane | Same Fleet journal/executor; no CLI terminalizer | KEEP; CLI direct/strict branch must not create secondary owner |
| Shared Plan `runtime/shared_plans.py` → `FleetPlanService` typed Plan ingress | Original Resolver/Fleet lifecycle, no shared-plan-owned transaction | Same source-bound original Run/Step route | KEEP; expand dynamic call trace in CO-R3 |
| Direct/Strict `services/fleet_run_control_plane.py:236` → `FleetRunLifecycle.admit:181` | Original serialized admission at `:282`, plan & revision at `:295`, admit `:306`, submit `:332` | Original placement and Run journal, original worker post-admission | KEEP; X0 checks actual ordered calls; no fallback |
| Scheduled `services/schedule_execution.py:124` → `_runs.start_bound:130` | `ScheduleExecutionStore` pending/CAS, original Fleet admitted child; separate `_cas` at `:151` | Child submit can precede schedule-state commit; journal replay/recovery | **STRENGTHEN CO-R2a**; `T-SCH-1..4`, crash and cancellation race |
| Schedule API `api/routes/schedules.py:74` → `services/schedules.py:94` | Schedule service/engine → same Fleet control plane | Same child Run journal, no alternate scheduler admission | KEEP with CO-R2a fault proof |
| Agent execution `integrations/fleet_plan_run.py` and `integrations/fleet_plan_run_execution.py:106` | Frozen original admitted Agent/Plan execution; immutable attempt, resource intent via Enforcer/LACE | Original native launcher, journal + teardown; no agent-owned runtime | KEEP; physical P3C/AG-03/04 still blocked |
| Strict sandbox `integrations/fleet_plan_run_execution.py:130` → `fleet_plan_process_isolation.py:94` | `select_bound_process_isolation_backend` requires admitted identity/capabilities; single chosen provider | Provider mechanism cannot sign, re-admit, or classify terminal | KEEP + CO-R3 native provider proof |
| Workspace request/allocate `runtime/workspaces.py:101,184` | `RepositoryWorkspaceAuthority`, original SQLite cut and `mkdir:196` | OS directory can persist when DB commit fails/crashes | **STRENGTHEN CO-R2b**; `T-WS-1,2` |
| Snapshot `runtime/workspaces.py:208` | Staged copy, `verify_clean_source_snapshot:254`, rename `:268`, `READY:285` original SQLite | FS/DB not atomic; postcommit verify at `:291` and cleanup `:293` | **STRENGTHEN CO-R2b**; `T-WS-3..5` |
| Candidate worktree `runtime/workspaces.py:302` | Original owner + Git + SQLite | Unsettled registered worktree metadata after crash | **STRENGTHEN CO-R2b**; `T-WS-6,7` |
| Workspace release `runtime/workspaces.py:635` | Original `RepositoryWorkspaceAuthority` with `rmtree:654`, final DB RELEASED | Crash between physical delete & durable transition; writer quiescence | **STRENGTHEN CO-R2b**; `T-WS-8,9` |
| Evidence `runtime/execution_evidence_acceptance.py:49` | `ExecutionEvidenceAcceptanceAuthority` durable validation `:80`, accept write `:85` in one `LocalDatabase` cut | Accepted evidence never authorizes physical LACE fact | **STRENGTHEN CO-R2c**; expiry in-cut `T-EV-1..4` |
| Lease `runtime/workspace_leases.py:206` | Existing `WorkspaceLeaseStore` same-transaction validity and quiescence | No second lease coordinator | KEEP; reuse timestamp semantics for evidence |
| Operational DB `runtime/database.py:180` | Only `LocalDatabase` may commit operational SQLite; DB0 AST census enforces direct connection/commit ownership | DB3–DB6 offline backup/ENOSPC observations do not authorize restore | KEEP + NATIVE QUALIFY; real disk/fsync/EIO still missing |
| Placement `runtime/executor_pool.py` / `_executor_placement_queue.py` | Existing reservation/capacity/CAS | One Fleet reservation, no independent queue | KEEP; retain generation binding |
| Repository effects `services/repository_effect_execution.py:39` / `repository_effect_settlement.py:42` | Existing journal and attempt owner | External effect observed and settled through same owner | KEEP; no second effect authority |
| Physical sampling/recovery `runtime/enforcer_lace_fleet_resource_owner.py:93,238,419` | Original authenticated Enforcer/LACE owner and run/step binding | Shared Linux low-level reader; independent physical fact gate, original teardown owner | KEEP + NATIVE QUALIFY; Gate B/P3C not proven |
| Game Tape/Scout derived projections | Read-only Game Tape and disposable Scout; never a write-side authority | Derived observation only, no operational recovery | KEEP; X0 scout observer protections remain |
| Certification `services/certification_frontdoor.py:164` → original gate | Authenticated original Run/Step receipt verification and external admission | Certification is an independent gate, not new Run execution | KEEP; Gate A/B/C and release BLOCKED |

## One guaranteed-path assessment

- **Checked executable source edges:** REST → control plane; Schedule → Fleet `start_bound`; Fleet `_start_intent` serialization → planning → recheck → admission → `_submit`; strict Plan → admitted selector; snapshot staging → original write transaction; evidence durable reread → same accept transaction. The new CO-R01 X0 check actively rejects negative call-edge mutations.
- **Already guarded independently:** raw SQLite commit/connection sites (`canonical_database_owner_contract.py` wired into X0); diagnostic/physical publisher fork (`x0_single_execution_contract.py`); Scout/observability projection (`scout_observer_contract.py`); QENV1 exact source and original native ring.
- **Not proven end-to-end:** arbitrary dynamic imports/plug-ins; all CLI subcommands, API mutation endpoints, every Shared Plan and Agent submode, provider/native physical certainty, crash/restart ordering of Schedule and FS/Git effects. These are **explicit CO-R2/CO-R3 tracing and fault-injection tasks**, not falsely marked complete by source-level X0 checks.

## Lifecycle acceptance map

| Boundary | Authoritative durable owner | Failure-cut and prohibited state | Exact next test |
|---|---|---|---|
| Workspace allocation / publication / release | `LocalDatabase` for facts; `RepositoryWorkspaceAuthority` for FS/Git operations | Orphan path, published-but-uncommitted `READY`, deleted committed READY, unknown teardown claimed `RELEASED` | `T-WS-1..9`, CO-R2b |
| Schedule child admission | Existing Fleet journal for admitted Run; occurrence CAS for schedule | admitted child unlinked after crash, cancellation losing admitted causal race | `T-SCH-1..4`, CO-R2a |
| Evidence acceptance | `ExecutionEvidenceAcceptanceAuthority` in original DB write cut | Expired/revoked lease accepted after precheck; duplicate on lost ACK | `T-EV-1..4`, CO-R2c |
| Backend applied-but-unacknowledged | Enforcer journal and independent LACE original recovery | blind retry, fallback, or false terminal absence without observed custody | `T-BE-1..5`, CO-R3 |

**Negative evidence is not a PASS.** A structural ownership check cannot certify kernel effects, source provenance from a deployment, real WSL fsync, or independent signers. Source-only closure for CO-R01 is distinct from lifecycle closure (CO-R2), provider conformance (CO-R3), and security/release gates.
