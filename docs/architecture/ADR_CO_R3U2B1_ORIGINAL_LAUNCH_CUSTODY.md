# ADR: CO-R3U2B1 — Refuse original native process launch without LACE custody

**Status:** Development implementation; not physical custody qualification.
**Parent:** CO-R3U2A. **Ownership:** Existing OH-Goon Enforcer/LACE and original Fleet effect owners; no new Hashmarks work.

## Decision

The existing non-issuing `require_independent_native_parent_custody()` gate remains the **only** LACE custody authority boundary. Until independently enforced host/kernel parent and namespace custody is actually provisioned, native launch/effects must fail before work. No process-scoped flag, local ticket, JSON, `stat`, name-based `rmdir`, descriptor existence or internal signature makes a native operation safe.

CO-R3U2A guarded original network creation/setup/cleanup, but constructing a network `ip netns exec` prefix and several cgroup process launch paths were not yet fenced. CO-R3U2B1 closes this gap **without constructing an alternative runner**:

1. `CgroupV2AttemptLease.command_prefix` rejects native cgroup-backed work before handing a caller executable arguments; both direct and nested launch modes use the same refusal.
2. The original `-S -c` child bootstrap has **no reliable LACE host-custody verification channel**, so its executable payload is an unconditional early refusal rather than a working, unqualified `cgroup.procs` writer. This is intentionally disabled until independently provisioned custody can be authenticated *inside the child* at the effect cut. Tests of simulated filesystem attachment have been changed to expect refusal.
3. `launch_nested_bubblewrap` refuses native nested and monitor cgroup operations **before any process move, pipe allocation, or Bubblewrap launch**, even if a caller injects a fake `move_process` callback.
4. `_apply_resource_limits` and legacy `_write_limit` refuse direct cgroup2 control writes before writing any controller bytes. The latter remains for backwards-compatible fake fixtures only; it is not a new live writer.
5. `RestrictedEgressLease.command_prefix` rejects direct `ip netns exec` launch prefix construction without the same original LACE custody gate.
6. The existing X0 architecture checker pins these original cuts and rejects their removal, reordering, and alternate nested imports. Focused hostile regressions assert no process execution, `cgroup.procs` mutation, controller writes, state-record retirement, native subprocess, or pipe creation without custody.

## Boundaries and trade-offs

- This is **fail-before-effect hardening**, NOT CO-R3U2B independent host-custody provisioning. It makes native workload launch refuse until host custody can be independently proved, including existing valid-looking lease identity fields.
- Do not re-enable the old inline child bootstrap on the basis of a Python token, environment flag, or source-side process ownership. The eventual host implementation must bind canonical authenticated Run/Step, trust epoch, executor generation, original resource inode and immutable physical isolation into one original effect owner, including child process attachment.
- `rmdir(name, dir_fd=...)` is not identity-conditional. A path/FD check can reject many substitutions and prevent a false ACK but cannot guarantee no adversarial name replacement at the destructive syscall. Parent mutation exclusion or a different safe primitive must be kernel-proven; otherwise refuse deletion.
- Historical integration fixtures simulate **post-custody** behavior only, and are labeled accordingly. They are not evidence of privileged Linux protection.
- Do not move observability/diagnostic evidence into OH-Goon from Hashmarks or introduce any Hashmarks-owned code. Current baseline dependency configuration is left unchanged.

## Completion status

**Development only**: hosted focused/adjacent rings and original architecture gate. **NOT PROVEN**: independently enforced privileged/delegated LACE host, exact-source native WSL negative controls, foreign writer/FD/mount/namespace absence, Gate A/B/C, APP PASS and release.

**Next**: CO-R3U2B2, provision and independently qualify the original Linux LACE host owner with mandatory authenticated generation-bound custody, or report BLOCKED rather than granting live effects.
