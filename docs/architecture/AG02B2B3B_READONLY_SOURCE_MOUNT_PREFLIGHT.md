# AG-02B2B3B1 — read-only source mount preflight (NOT a physical mount)

**Parent:** AG-02B2B3A (`c692dc5aaa69d21a18406dfcce61560aebe24852315e99da0baaacba86c7d136`).

## Why

A signed host-private staging receipt only means bytes were written and read
back earlier. It does not prevent the stage changing before original LACE
Bubblewrap consumption. The original attempt storage owner now reopens **only
the staged attempt tree**, descriptor-anchored with `O_NOFOLLOW`, verifies the
complete expected frozen manifest, rejects extra/missing directory members,
symlinks, hardlinks and permission drift, and detects changed open file/directory
identity during the scan. The original AG-02A/B2A source paths are not reread.

`FleetPlanStepAttempt._inspect_agent_prework_readonly_mount(handoff, stage)`
returns an ordinary `OriginalSandboxMount` with a fixed target and bounded
identity. The original B23 `OriginalMountIntentDescriptor` can include this
mount and its identity will differ from a descriptor omitting it. **This method
does NOT mint an authenticated `RESOURCE_INTENT`, authorize an effect, invoke
Bubblewrap or claim native mount facts.** The original Enforcer and LACE
authorities remain unchanged. No new execution path or fact publisher.

## Remaining blockers / required next implementation

1. The original signed `AGENT_SOURCE_PREWORK_STAGED` receipt must be verified
   through original journal/issuer authority at the actual attempted mount;
   a caller-supplied dictionary is not authentication.
2. Bind the proposal into the **same original pre-effect** `RESOURCE_INTENT`
   (including source, target, hash and original Run/Step/attempt lineage).
3. Mount through existing Bubblewrap `_prepare_mounts` only after real original
   signed intent, revalidate exact FD-anchored stage immediately at consumption
   and reject any TOCTOU/mountpoint collision. Supply a securely mounted read-only
   tree, not a host-private directory masquerading as physical custody.
4. Have original shared LACE kernel observer independently inventory the actual
   bind mounts, namespace holders and mount FDs; prove absence and original
   teardown after crash/cancel/recovery. B27 resource provenance remains
   a prerequisite.
5. Prove original signer/root and native WSL with hostile corruption/races;
   enable no agent launcher, network, MCP or model disclosure before AG-03/04.

## Evidence classification

This is a hosted, no-effect preflight. It improves data integrity checks but
**cannot** certify physical read-only mount custody, independence, absence,
Gate A/B/C, APP PASS or release.
