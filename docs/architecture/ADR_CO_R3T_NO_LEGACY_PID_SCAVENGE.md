# ADR CO-R3T — Legacy orphan recovery cannot mint physical custody

**Decision:** Accepted for the legacy fail-before-effects boundary on 2026-10-15. **Security gate:** development only; independent kernel-enforced physical custody remains NOT PROVEN.

## Why

Prior non-LACE automatic orphan reconcilers used dead process identity and local resource records to invoke cgroup kill/remove, network namespace/link/nft deletion, and durable record retirement. An attacker or foreign host participant can retain a namespace or exchange a cgroup name at a destructive syscall boundary. A recorded cgroup (device, inode) pin and process death are not independently enforced exclusive mutation custody. Linux `rmdir(name, dir_fd=...)` cannot atomically assert that the name still identifies the expected child inode.

## Canonical owner map — active physical effects

| Original owner | Effect and admission | Retained guarantee | Independent proof still needed |
| --- | --- | --- | --- |
| `services/fleet_run_lifecycle.py`, `runtime/enforcer_lace_fleet_resource_owner.py` | Signed Run and Step, signer epoch, original resource-intent and effect-time authorization | Existing CO-R3P/Q original writer and transaction | Native trust roots, revocation at kernel effect, Gate A |
| `integrations/_fleet_plan_cgroup_resources.py` | Original cgroup allocation, limit/attach, kill/remove, records | Inode-bound local handle, pending creation quarantine; **legacy dead-PID recovery now rejects destructive work** | Independently owned cgroup parent; hostile rename at syscall; restart cleanup authority |
| `integrations/_fleet_plan_restricted_egress.py` | Original network namespace, veth and nft setup/cleanup | Ordinary active lease teardown unchanged; **legacy dead-PID recovery now rejects deletion** | Independent namespace/link/table custody, foreign holders and recovery authorization |
| `integrations/fleet_plan_local_lace_native_reaper.py` + `runtime/enforcer_lace_fleet_resource_owner.py` | Existing journal-ticket local LACE orphan recovery and original native callback | Original ticket and fenced sequence remain unique; no new reaper | Independent physical owner/custody for destructive network and cgroup effect, Gate B |
| `integrations/fleet_plan_bind_source_custody.py`, `fleet_plan_atomic_runtime_authority.py` | Opened mount/FD execution source | Original immutable descriptor and native execution source verification | Cannot prove all foreign handles/writers absent |
| `runtime/workspaces.py` | Native Git worktree writer-bearing retirement | Already refuses ever-started writer without independent absence | CO-R2b complete custody and safe Git deregistration |
| `integrations/linux_physical_observer.py` | Read-only native observation | No mutation authority and no claims of global absence | Independent kernel witness; not an authorization token |

No new execution owner, scheduler, signer, LACE recovery actor, sensor or Hashmarks dependency is introduced.

## Change

1. The existing `_reconcile_stale_cgroups` still checks record format, ownership, phase, pinned kernel identity and symlinks, **then always quarantines stale non-LACE identity-bound allocations** instead of calling `_kill_cgroup`, `_wait_for_cgroup_empty`, `_remove_cgroup` or unlinking the recovery record. The original journal-owned path remains separate and the active attempt `close()` remains unchanged.
2. The existing `_reconcile_stale_egress_leases` still checks native name coherence and owner liveness and refuses LACE-intent PID scavenging; stale non-LACE network records now **fail before** native delete/retirement. Normal explicit active-lease cleanup and native post-delete observations remain within the original owner.
3. Spoofed `physical_custody` or a fabricated epoch in an orphan record confers no authority. An orphaned or absent name does not confer permission to delete or retire bookkeeping. No new issuer/verification capability is implemented.
4. The original X0 architecture checks and QENV1 CORE ring are amended to enforce this stronger boundary and preserve original active-attempt handle/cgroup tests. Stale old mutation tests are updated to mutate the actual original effect owner rather than a previous deleted legacy route.

## Consequences / qualification

- Dead legacy records remain and may block new native allocations. That is **intentional quarantine**, not automatic recovery completion; operator must use a separately qualified physical/recovery authority.
- This closes one unsafe automatic destructive route. It **does not yet** stop the original LACE journal/native reaper from attempting cleanup under its authenticated original ticket, nor does it establish kernel-enforced parent custody for normal active-attempt `close()`; both are physical Gate B blockers.
- A typed, immutable generation-bound native custody capability cannot be implemented as a trusted authorization solely from Python, SQLite, PID checks, path checks or this ADR. CO-R3U must supply an independently provisioned, kernel-backed verification contract before enabling any broader destructive effect.
- Real WSL native cgroup-v2, signer provisioning, Gate A/B/C, P3C, writer-bearing Git, APP PASS and release are NOT PROVEN/BLOCKED. Hosted mocks do not qualify them.
