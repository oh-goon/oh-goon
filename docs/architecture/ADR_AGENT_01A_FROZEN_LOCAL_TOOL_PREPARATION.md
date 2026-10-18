# AGENT-01A — Frozen read-only tool preparation in the original Step owner

**Decision:** extend `integrations/fleet_plan_step_execution.py`, not a new
execution engine. Consume exactly the Resolver-owned AG-02
`FrozenAgentStepHandoff` and stage its already verified repository and Skill
member bytes into an ordinary attempt-private working tree. Emit one typed
`ResolvedCommandStep` for a bounded fixed-string `rg` search; execution remains
the original Step executor's responsibility.

**Scope:** explicitly selected `harness: local-search`; `task: search:<literal>`;
read-only repository request; zero model recipients; zero network egress; no
other tool capabilities. `SKILL.md` and captured resources are present as data,
never interpreted as executable commands. No-match exit code 1 is an
observation. Timeout, cancellation, process cleanup and output capture belong
to the existing command Step/supervisor.

**Not yet admitted:** producing a `ResolvedCommandStep` from prework does not
authorize dispatch. The existing original Agent Resolver and Fleet Agent cut
remain preview-only/refused. Future AGENT-01B must bind these exact bytes to
original admitted Fleet Step/attempt authority, route to selected backend, and
publish original Game Tape outcomes; it must not make a second Run/Step engine.
Native Hashmarks/WSL qualification remains separately open. This checkpoint
requires no high-security host qualification or privileged filesystem flags.

**Invariants:** source capture verified once; no live source reopen after
capture; task is data not shell; fixed `rg` argv without shell interpolation;
normal bounded owned process lifetime; no model recipient/no write/no network
scope; every new defect receives a focused real-process regression.
