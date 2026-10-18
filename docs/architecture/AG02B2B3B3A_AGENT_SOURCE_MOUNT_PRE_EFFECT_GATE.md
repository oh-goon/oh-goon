# AG-02B2B3B3A — original LACE pre-effect Agent source mount gate

**Checkpoint class:** hosted development; fail-before-effects security regression. **Parent:** QENV1 exact source SHA-256 `0f1885917be5f2d64d9c549f42223e86ed244fcdcc305a6a5a17ea8cbc68921d`.

## Decision

Retain the existing `IsolatedProcessRequest` → `LocalLaceJournalReservation.reserve` → original `RESOURCE_INTENT` → Bubblewrap `_prepare_mounts` call chain. Agent prework bytes currently have an authenticated staged receipt and a preflight `OriginalMountIntentDescriptor`, but that descriptor has **not** become authenticated, physical LACE mount authority. A caller may not promote it by placing its staging directory in an ordinary managed input, host-tool, workspace bind, or by directly using the reserved Agent source target.

One guard, `require_original_agent_source_mount_authority`, lives in the existing `fleet_plan_local_lace_intent.py` module. The original resource projection invokes it before any signed intent is written. The Bubblewrap entry invokes it before native allocations, and the original mount builder retains the same invariant at consumption. It checks both lexical and resolved paths to block aliasing of attempt-private staged content; it does not reread repository/Agent Skills source data or create runtime authority.

## Scope and nonclaims

This checkpoint **does not** mount Agent files, verify read-only behavior inside a kernel sandbox, commit a signed Agent mount intent, authorize an Agent Step, or publish any physical quiescence/teardown claim. Existing non-Agent managed inputs continue on the same code path. AG-02B2B3B3A explicitly prevents unsafe early activation of the proposed Agent source mount.

## Next acceptance criteria: original resource-intent / Bubblewrap integration

1. In the existing original Fleet Step-attempt owner, validate a single committed original `AGENT_SOURCE_PREWORK_RESERVED` and `AGENT_SOURCE_PREWORK_STAGED` pair, exact source identity, Step attempt, epoch, lease and signer generation *in the same transaction* as the original mount-resource reservation. No caller-created proof or second issuer.
2. Prepare one immutable input binding from the already frozen stage. The prepared mount descriptor must exactly match all native bindings projected into the original signed `RESOURCE_INTENT.resources.mounts`; unknown, extra, reordered or substituted mounts fail before effects.
3. Ensure Bubblewrap consumes that exact input read-only using a descriptor-pinned/TOCTOU-safe host source; digest validation alone before mount is insufficient. Continue to use the original `_append_managed_inputs` path, not a new agent-specific executor or mount builder.
4. Independently observe actual mount, FD, namespace and descendant ownership under B27's complete Run resource envelope, including crash/restart and post-cleanup absence. An API success or namespace view alone is not physical Gate B proof.
5. Only then replace the fail-closed denial with the authenticated, physically qualified original owner check. Retain negative regression and X0 authority census. No Agent Step execution until AG-03/04 enforced disclosure and all required admission/security gates are qualified.

**Architectural invariant:** prepare/verify Python environment once (QENV1), then isolated rings; no `uv`/`pip` mutation inside rings. Resolve once, transport once, execute once, report once. One canonical OH-Goon runtime.
