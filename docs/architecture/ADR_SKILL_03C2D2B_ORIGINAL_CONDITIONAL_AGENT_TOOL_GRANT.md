# SKILL-3C2D2B — Original conditional Enforcer Agent tool grant

## Problem

The previous original Fleet cut authenticated the source, attempt and proposed tool scope but did not record an Enforcer grant for the exact operational tool invocation. A scope inspection and a signed prework or stage receipt are not process-launch permissions.

## Decision

The same `OriginalFleetLocalLaceReservation.reserve()` transaction invokes `CausalRunJournal.issue_original_agent_tool_launch_grant_in()` before attempting the existing `LocalLaceJournalReservation.reserve()`. The original implementation performs the existing read-only scope inspection (one policy owner) and binds the grant to Run, Step, operational attempt identity, attempt number, command ordinal, current control lease and epoch, original source/stage event sequence, admitted contract, executor generation, signer trust epoch, requested-effect and independent ceiling identities, exact proposed command identity and tool name.

The grant uses the existing original `ReceiptAuthenticator` with the distinct `agent-tool-launch-grant` domain and the original causal journal. It is single-use per Step/attempt/ordinal. The original verifier rejects malformed, re-signed inconsistent, missing-ancestor or replayed grant events. It is never a detached bearer token or separate runtime service.

`effect_permission=NONE_UNTIL_LACE` is deliberate. An Enforcer grant is **conditional**, not permission to operate on the host. The original `RESOURCE_INTENT` Agent prohibition still applies, so the production Fleet mutation rolls back any newly prepared grant when native containment cannot be established. This is fail-before-effects. Neither source-stage proof, command scope, nor grant can be consumed in place of a signed physical `RESOURCE_INTENT`, atomic launch confinement, native mount observations or teardown proof.

The issuer/provisioning policy, Plan resolver, agent scheduler and physical backend remain unchanged. Ordinary command Steps do not traverse the new Agent grant path.

## Verification and limitations

Focused regressions cover distinct ordinals, exact positive grant binding, replay refusal, wrong attempts, changed commands, tools, network, workspace and lease, re-signed stage-link tampering, and strict Agent `RESOURCE_INTENT` refusal. Hosted tests do not qualify kernel isolation, native WSL, source-mount custody, process lifecycle or release gates.

## Follow-on

SKILL-3C2D2C must make the original physical `RESOURCE_INTENT` (not this grant alone) accept only fully bound read-only Agent source mounts, authorize the grant at the actual LACE effect cut, prove native mount/process custody and teardown, and leave write, model-disclosure and network effects independently blocked until qualified. The original resolver currently prohibits Agent Step dispatch and must not be lifted prematurely.
