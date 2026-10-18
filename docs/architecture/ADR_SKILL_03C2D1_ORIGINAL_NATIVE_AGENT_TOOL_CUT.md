# ADR SKILL-3C2D1 — Original Fleet/LACE native Agent tool-scope cut

## Decision

The first runtime effect inspection for a future native Agent tool launch is performed **inside the existing original Fleet session-owned SQLite transaction**, at `OriginalFleetLocalLaceReservation.reserve`, immediately before the existing `LocalLaceJournalReservation.reserve` and before native resource allocation. No new signer, journal, permission registry, scheduler, or runner is created.

`CausalRunJournal.inspect_original_agent_tool_launch_scope_in` verifies the original admitted Run/Step signed envelope, source-prework reservation and stage, live control epoch and lease, active and originally admitted signer trust generation, and reuses the existing single Enforcer request-versus-ceiling checker. Its input for the proposed native effect is the actual process argv, the real prepared host-tool roster, the actual egress configuration and whether the process has a read-only workspace.

The first permitted *scope-inspection* shape is deliberately small: `read_only` repository, zero model recipients, no network egress, an actual read-only workspace, and an executable equal to the canonical `/toolchain/bin/<declared tool>` binding present in the prepared host-tool roster and in the signed demand. A renamed executable, `PATH` search, shell trampoline, extra tool mount, writable workspace, recipient, write request, or network access is refused **before** the native reservation. This is an inspection result (`None`), **not** an effect grant or bearer capability.

The original `publish_local_resource_intent_in` Agent Step refusal remains in force, so even an inspected allowed tool **cannot launch**. `AGENT_SOURCE_PREWORK_RESERVED` and `AGENT_SOURCE_PREWORK_STAGED` remain `effect_permission = NONE`, do not authorize a mount, and cannot act as process authority. The original Bubblewrap process and native mount/FD checks are unchanged. Older command workloads are not routed through the added Agent inspection.

## Reason

A deployment upper bound alone does not constrain an actual process argv, tool roster, network setting or writable workspace. Inspecting that exact effect at the original Fleet/LACE cut is necessary for future scoped grants and removes an entire class of unverified permissive launch assumptions without introducing a parallel authority.

## Unclosed conditions (must not be inferred from this checkpoint)

1. Original `RESOURCE_INTENT` must positively bind the **same exact operational Agent attempt** to its signed staged source mount, then LACE must physically enforce readonly mounts and tool/process confinement.
2. Effect-time **positive grants** must bind Run, Step, operational attempt, ordinal, control epoch, executor generation and signer-trust epoch, and be revocable on cancel, restart or trust change. There are **no such grants** in SKILL-3C2D1.
3. Repository writes require a separate physically enforceable write fence. Model recipients require an original disclosure gateway, and network destinations/protocols require LACE egress enforcement. These are **not** authorized by a read-only tool scope inspection.
4. Hostile-writer/native Linux/WSL LACE, teardown, recovery, APP PASS and release remain unqualified.

## Regression scope

See `backend/tests/unit/runtime/test_skill03c2d1_original_tool_effect_cut.py`, existing SKILL-3C signed-demand regressions, and the canonical AG-00 architecture test. The previously reported native-issuer SQLite `no-cut` nested-transaction baseline issue remains independent and open. No Hashmarks responsibility changed.
