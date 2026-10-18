# B3B3B4 — Signed read-only workspace mount completeness

**Authority:** the existing `OriginalMountIntentDescriptor` and original signed
`RESOURCE_INTENT`, consumed by the existing Bubblewrap builder. Not an Agent
runtime, a second resource-intent publisher or a native-containment certificate.

## Defect

Prior to B3B3B4, the original `mounts` digest covered managed inputs and host
-tool snapshots but not `IsolatedProcessRequest.read_only_workspace`, although
Bubblewrap could mount its `snapshot_path` read-only at `/workspace`. An
attacker with influence over the prepared request could change which snapshot
was mounted without changing the signed mount identity. The separate `workspace`
resource field did not bind this optional actual source.

## One-owner correction

The **same original mount descriptor** now includes an optional typed
`read_only_workspace: OriginalSandboxMount` containing the exact host source,
`/workspace` target and frozen snapshot content SHA-256. If there is no
read-only workspace, the pre-existing signed digest and wire JSON are unchanged.
The descriptor forbids shadowing the workspace target with another binding.

The existing reservation projects the source into that descriptor, writes the
original `RESOURCE_INTENT`, and returns the signed mount identity. The existing
Bubblewrap executor rechecks the complete descriptor before cgroup creation;
its existing mount builder rechecks again before composing the `--ro-bind`.
No duplicate hash implementation or second admission path was added.

## Anti-drift and proof boundaries

The original X0 architecture checker rejects removing the descriptor field,
source, SHA, or consumption check. Hostile tests mutate all four and assert
rejection, including mutations between signed reservation and native effects.
QENV1 prepares and verifies dependencies once and uses the prepared interpreter
for isolated test rings. Native WSL must be run on the exact candidate.

This closes a signed *workspace* source/target identity gap, not the broader
completeness of all root/writable/atomic runtime mounts, native inode pinning,
physical observations, independent Gate B, or AG-03/04 disclosures. Agent Step
execution remains denied at the original `RESOURCE_INTENT` owner. Its staged
source still needs original signed effect admission plus qualified physical
custody. Until then fail before any Agent physical work.
