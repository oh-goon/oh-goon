# AG-00 — original OH-Goon execution ownership and agent-hosting boundary

**Status:** exact-source development architecture census, NOT native execution or production certification.  
**Parent:** `OH_GOON_B26_CANDIDATE.zip` SHA-256 `f941652e242ab3c0f85d0167d4d58b03106dfc77479f0271fb9303033f0bcf6d` (2,806 verified members).  
**Decision:** [ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md](ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md) is accepted. No separate agent runtime.  
**Constraint:** resolve once, transport once, execute once, report once. No new policy, effect, observation, recovery, or terminal writer.

## Original source call-edge census (not an imagined agent call path)

| Concern | Verified source and concrete call edge | Authority | Agent extension rule |
|---|---|---|---|
| Compiled meaning | `services/fleet_plan_resolver.py` through `integrations/fleet_plan_compilation.py` and `services/fleet_plan_source_graph.py` into `ResolvedFleetPlan` | Original compiler / Resolver | Agent skill+repository closure may only be frozen here; no second parser |
| Direct/Strict/Schedule ingress | `services/fleet_run_control_plane.py::FleetRunControlPlane.start_prepared_plan` and `.start_bound` both delegate `._start_intent`; `.start` rejects plan-shaped generic commands | Original Fleet admission / lifecycle | Any agent Step must enter via original prepared Plan target |
| Original Run activation | `services/fleet_run_execution.py::FleetRunExecution.execute` calls `self._lifecycle.activate`, `self._run`, `self._lifecycle.finish_detail`; `_run` calls `self._lifecycle.begin` and `self._targets.execute` | Original FleetRun lifecycle, leases and target scheduler | No agent scheduler, direct launcher, replay, or terminal fork |
| Direct+strict Step convergence | `integrations/fleet_plan_run_execution.py::FleetPlanRunExecutor.execute_direct_run` and `.execute_strict_run` both construct `FleetPlanStepSequence` using the same `self._step_runtime`; each invokes `sequence.run_direct`/`sequence.run_strict` | Original sequential Step runtime | Only a *post-admission typed workload adapter* may differ; agent variant NOT implemented |
| Effect admission and lease | `services/fleet_admission.py` and `services/fleet_run_lifecycle.py` with existing original `runtime/execution.py` / effect coordinator | Original Enforcer and Fleet lifecycle | Agent must not provide an issuer, lease or resource intent |
| Sandbox dispatch | `integrations/fleet_plan_bubblewrap_isolation.py::BubblewrapProcessIsolation.execute`; `fleet_plan_opensandbox_isolation.py` for remote provider, via prepared original Step execution | Original backend choice/Step executor | No fallback or parallel agent subprocess dispatcher; B27 is a separate physical dependency |
| Local physical intent | `runtime/enforcer_lace_local_resource_intent.py::reserve` publishes original `RESOURCE_INTENT`, then verifies journal cut; `observe` is a development reservation adapter | Original signed Run journal / LACE owner | Do not move fact publication to tool/harness, observer, or agent-specific code |
| Post-crash read-only observation | `services/fleet_run_execution.py::resume_local_lace_takeover` -> `self._lifecycle.local_lace_recovery_owner` -> `runtime/enforcer_lace_fleet_resource_owner.py::sample_recovery_physical_unqualified` -> shared `integrations/linux_physical_observer.py::observe_kernel` | Original recovery owner + one Linux kernel mechanism | Signed-bound, read-only, unqualified; no physical quiescence claim |
| Immutable facts + terminal | `services/fleet_run_lifecycle.py` controls final Run transaction; `runtime/enforcer_lace_journal.py` owns causal local-resource facts; `runtime/journal_store.py` carries original Journal custody | Original journal + Enforcer terminal classifier | Agent statements are not terminal authority |
| UI | `runtime/game_tape_live_output.py` and existing frontend Runs projection | Read-only evidence projection | Never publish facts or launch jobs from Game Tape |

**Critical qualification note:** these are source-level call edges, not independent runtime traces across every ingress. Original external signer/six independent roots, native B26, Gate A, per-backend physical Gate B, Gate C, Q0 and APP PASS remain BLOCKED. Hosted AST tests cannot prove semantic call-chain uniqueness through Python dynamic dispatch. AG-02 and AG-09 must establish actual dynamic Command/Agent parity with correctly provisioned Run identities. Native command execution in an actual admitted signed Run is **NOT RECORDED** here.

## Guard strategy and mutation scope

AG-00 strengthens the **existing** `x0_single_execution_contract.py` check (no second gate entrypoint). The guard verifies the concrete canonical ingress/lifecycle/Step handoff anchors above, prohibits new agent-specific CLI effect entrypoints or authority-owning classes, and bans direct process/DB/journal effect creation in new agent-named production adapter modules. Focused mutants insert an alternate agent dispatcher, policy/enforcer, scheduler, journal, local observer, dynamic subprocess or agent-owned CLI; they must fail architecture checks before work. Python's dynamic import/reflection escape paths still require AG-02+ runtime adversarial tests; architecture checks never become a security sandbox.

## Ownership and future work

- **AG-01** next: freeze skill+repository source and acceptance contracts in the *original* Resolver/source graph; read-only inspection only.
- **B27** parallel: extend *original* Run-owned Bubblewrap mount/FD/namespace/descendant envelope; do not fork physical mechanism.
- **AG-02 and onward** require authoritative type/capability and original entry-point parity; AG-05 real harness effects and AG-07 loops must not be enabled before their physical/admission dependencies.
- Never merge a parallel agent Runtime, LoopRunner, StepScheduler, Admitter, physical fact publisher, Journal or Game Tape verdict writer.
