# AG-02B2B3B2 — original signed-stage mount-intent preflight

**Parent source:** repaired `OH_GOON_AG02B2B3B1_FIX_CANDIDATE.zip`.
**Status:** hosted development-only. No mount has been admitted or created.

## Architecture

This checkpoint closes an authority hole in the earlier no-effect mount proposal.
`FleetPlanStepAttempt._inspect_agent_prework_readonly_mount()` correctly
revalidates staged bytes but accepts a stage receipt as data. A caller-provided
receipt, even if internally consistent, is not original Fleet authority.

`FleetRunLifecycle.inspect_original_agent_source_mount_for_attempt()` is a
read-only extension of the **existing** live Fleet session owner. It observes
one original `ExecutionSession.mutate()` cut and requires:

- The original strict, Bubblewrap-bound execution authority and non-cancelled
  session with live lease, provisioner and unchanged enrollment.
- Exactly one matching original operational `STEP_ATTEMPT_CREATED` for the
  repository, operation, Step, attempt number and canonical storage path.
- A nonterminal attempt, live signer trust grant and signed original admitted
  signer epoch in the same authenticated causal journal.
- Exactly one authenticated `AGENT_SOURCE_PREWORK_RESERVED` and linked
  `AGENT_SOURCE_PREWORK_STAGED` event for the **same** Step, attempt digest,
  executor generation, lease, epoch and frozen handoff identity.
- The original `FleetPlanStepAttempt` reinspection of staged bytes, inode and
  filesystem ancestry. Original repository/skill source paths are never read.
- A second execution-authority and cancellation check before returning.

The return value is an ordinary `OriginalMountIntentDescriptor`, whose
`OriginalSandboxMount` is fixed to `/opt/oh-goon/agent-source`. It carries
**no effect permission**. No new parser, agent runner, signer, SQL connection,
resource-intent publisher or physical observation owner is introduced.

## Explicitly not completed

- No original `RESOURCE_INTENT` incorporating this descriptor has been signed
  or accepted; the method is a no-effect preflight, not a second admission.
- The existing Bubblewrap `_prepare_mounts` is unchanged. No native source
  bind, consumption-time FD custody, independent mount/FD observer, cleanup
  absence or Gate B proof is claimed.
- An authenticated issuer/root provisioned by the real operating environment,
  native WSL qualification, physical B27, AG-03/04 data-disclosure controls,
  APP PASS and release are separate blockers.

**Next integration:** carry the descriptor through the existing original
`LocalLaceJournalReservation.reserve()` before any native effects, verify the
mount descriptor equals the signed `RESOURCE_INTENT.resources.mounts` and
ensure the existing Bubblewrap backend consumes only that same bound identity.
Physical custody must separately prove no TOCTOU and real absence on cleanup.
