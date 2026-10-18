# AG-02B1 — Typed Agent Step and original-Resolver prework handoff

**Parent:** `OH_GOON_AG02A_CANDIDATE.zip` SHA-256 `dd83914ce9f4a598d38ceeb50f3bc95093000865e1ab755cdadd0b2a0011b923`.
**Authority:** `docs/architecture/ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md`.
**Classification:** hosted DEVELOPMENT, no Agent Step execution or admission.

## Why

AG-02A retained byte-exact local repository and Agent Skills source while
`resolve()` correctly rejected all agent work before effects. AG-02B1 adds
one typed Step to the **existing** Plan/Goon compiler and one strongly bound
read-once handoff for later original admission. It is *not* a new workload
executor, YAML parser, runtime or independent transport channel.

## Implemented

- `uses: agent` is a typed Atomic Goon Step (`AgentPlanStep`), in the original
  PlanStep discriminated union, with a bounded harness name, bounded task and
  timeout. It has **no** argv, shell, environment, credential, provider
  endpoint or executable backend binding. Extra fields are rejected.
- The original source graph counts ordered Agent Step **occurrences** across
  imports, not unique source files. Reusing one Goon twice cannot produce two
  executable occurrences from one handoff.
- `FleetPlanResolver.prepare_agent_step_handoff` analyzes the same original
  Plan/Goon graph once, checks the exact root revision and existing input
  binder, captures AG-02A repository and Skill bytes once, then returns a
  verified `FrozenAgentStepHandoff` bound to authored owner source SHA,
  step occurrence, harness, task, timeout and complete graph/source capture.
- The handoff is process-memory prework evidence. Its immutable content and
  domain-separated identity can be verified without reopening host paths.
  Repeated capture of changed local bytes or changes to imported Goons
  changes the identity; stale/invalid/tampered handoffs reject.
- Every original `resolve()` / `resolve_source_graph()` still rejects all typed
  agent inputs or Agent Steps before work. No original Enforcer admission or
  signed Run/Step custody has been created and no external harness launched.
- The existing `qualify-development-wsl` script adds one independently
  supervised `AGENT_PREWORK` ring with exact modules and required reporting.
  Hosted regressions do not substitute for a future native witness.

Example *authoring only* (cannot execute):

```yaml
# Atomic Goon: inputs are declared by the normal existing compiler
inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
  - uses: agent
    harness: opencode
    task: review the authorized repository
```

## Strong security limits / explicit exit criteria

**Not implemented:** original provisioner-verified pinned GitHub source
acquisition; durable one-shot immutable **signed** Run/Step source transport;
agent Step addition to the admitted `ResolvedPlanStep` discriminated union;
one original Step sequencer dispatch; original Resource Intent/LACE sandbox
binding; provider/MCP/network disclosure policy; independent physical custody;
real native WSL run. These are not silently provided by `FrozenAgentStepHandoff`.

**Next AG-02B2:** original Enforcer-admitted transport transaction binding
existing `FrozenAgentSourceCapture` bytes to original Run/Step/generation and
source identity, one-shot and replay-rejected; no second loader/scheduler.
Only after AG-03/AG-04 and independent Gate A/B may a narrow selected external
harness adapter receive the admitted immutable source. B27 remains parallel.

**Frozen invariant:** resolve once; transport once; execute once; report once.
