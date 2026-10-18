# SKILL-3C2D2C1 — Original Agent staged source in native bind roster

## Problem

SKILL-3C2D2B issues one Enforcer-authenticated conditional per-attempt, per-ordinal tool decision, but LACE has no native bind roster representation of the exact AG-02 staged source associated with that grant. An independent Agent mount list would violate the single owner contract. Treating the signed stage alone as an effect permit would be unsafe.

## Decision

Keep the single original `OriginalFleetLocalLaceReservation`, `CausalRunJournal`, `OriginalMountIntentDescriptor`, `prepared_original_native_mounts`, `OriginalBindSourceLease`, and Bubblewrap argument builder. Add one optional immutable `OriginalSandboxMount` projection to `IsolatedProcessRequest` for the staged Agent source.

* `CausalRunJournal.original_agent_source_mount_for_grant_in` projects only the original verified staged mount after matching the signed tool grant sequence, Step, exact operational attempt, attempt root, and command ordinal in the current session transaction. It is a projection, not a second issuer.
* The original Fleet reservation compares that source with the supplied native request after the original Enforcer decision. Absent or altered proposed source fails within the transaction.
* The single native mount roster includes exactly `--ro-bind <operational-attempt>/agent-source-prework /opt/oh-goon/agent-source` when the optional proposal is present. The existing mount descriptor binds its content identity; the same roster is used by Bubblewrap validation and Linux `O_PATH` FD pinning.
* Native roster permits no unsigned Agent target, writable Agent bind, alternate source, duplicated target, or managed sandbox shadowing. Existing command-step serialization remains unchanged if the optional source is absent.
* The original Agent source refusal remains active at both native intent derivation and Bubblewrap execution. No new positive `RESOURCE_INTENT`, process creation, network, repository write, model disclosure, or Agent dispatcher is enabled.

## Verification

Hosted regression tests inspect exact source ancestry, ordinal/attempt substitution, native descriptor, real Linux O_PATH mount-source pin, read-only Bubblewrap argv correspondence, and the unqualified native-effect refusal. The Bubblewrap builder test isolates argument construction by substituting the refusal **only within that unit test**, and is not an Agent execution or security qualification.

## Remaining physical work

The next checkpoint must atomically bind original Enforcer grant and `RESOURCE_INTENT` to the exact staged directory plus content through the original Fleet owner, prove read-only kernel mount custody at process launch, revoke on trust/lease/cancel change, prove native process stop and teardown, and qualify on the target Linux/WSL host before unlocking any Agent dispatch. Directory inode pinning alone does not freeze mutable descendants. Scope-specific write, disclosure and network cuts remain separately blocked. No APP PASS or release assertion.
