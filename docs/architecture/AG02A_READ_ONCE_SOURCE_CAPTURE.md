# AG-02A — Original Resolver read-once source capture (development only)

**Date:** 2026-10-13  
**Parent:** exact AG-01 source, SHA-256 `b72829fdaadc7797df6c912fab057da9cd65723d2408436df0e01ced8ea9be3b`  
**ADR:** `ADR_AGENT_WORKLOAD_CANONICAL_EXECUTION.md`

## Purpose

AG-01 provided bounded content identity for an authorized local repository and an
Agent Skills package, but only retained hashes during prework. Re-opening a path
later to obtain executable bytes would violate **resolve once; transport once;
execute once; report once**, and introduce a source-replacement race. AG-02A
closes the *read-once capture* portion of that gap on the existing Resolver
source-graph and input-binding boundary.

## Implemented

- `FleetPlanResolver.capture_agent_sources` uses the **existing source graph**,
  root revision check, projected-input binder and single AG-01 source reader.
- The same source walker hashes and retains exact file bytes once in immutable
  `FrozenSourceMember`/`FrozenSourceTree`/`FrozenAgentSourceCapture` values.
- Root Plan and every imported Goon's source identity are covered by an
  original-graph closure digest. A changed child Goon changes the capture
  commitment even if the Plan file is byte-identical.
- Local source traversal uses fd-anchored `openat`-style directory/file opens,
  `O_NOFOLLOW`, stable ancestry and file identity checks, bounded depth,
  entry/file byte budgets and same-device constraints. Unsupported native fd
  primitives **fail closed**, rather than falling back to pathname reads.
- Captured content and provenance have an explicit domain-separated identity.
  Verification recomputes the original per-file and full-tree commitments.
- No archive writer, downloader, second CLI, agent launcher, source reopening,
  runtime resolver, policy, Enforcer, LACE or journal owner was added.

## Security/authority limitations

`CAPTURED_PREWORK_NOT_ADMITTED` is **not** `RUN_ADMITTED`, secure source transport,
physical containment, custody, or authorization to disclose repository content.
The raw captured bytes remain in the caller's process memory; do **not** log
or expose them through CLI/API/UI. The preview remains metadata-only.
Ordinary original `resolve()` still rejects every typed agent source input
(including hidden imported Goon defaults) before executable work.

Pinned GitHub skills remain blocked until original-authority source acquisition
can attest immutable content. A complete source and skill capture is not a
permit to call an LLM/MCP/GitHub endpoint. Enforcer must separately authorize
recipients and LACE/backend must physically constrain reachable resources.

## Independent future checkpoint: AG-02B

1. Preserve one canonical source owner and have the original acquisition
   authority verify pinned remote content (no second GitHub downloader).
2. Add the typed Agent Step representation to the **existing** compiler and
   original Step sequencer, with no process/agent effects before Gate A and
   required backend/disclosure capabilities are qualified.
3. Commit exact captured bytes via the original signed Run/Step source custody
   transaction **once**, verify per-file/aggregate payload identity before any
   sandbox effect, then deliver the immutable payload only to the admitted
   runtime. Reject missing/changed/duplicate/replayed transport.
4. Preserve original reservation, RESOURCE_INTENT, sandbox selection,
   LACE observation, journal, cancellation, recovery and terminal classification.
5. Verify Command/Agent cross-ingress owner parity. B27 actual physical resource
   binding, Gate A, native local Gate B, remote Gate B (if enabled), Gate C,
   APP PASS and release remain separate unqualified gates.

## Regression obligations

`test_ag02_frozen_agent_source_capture.py` includes exact-byte and no-reread
capture, hostile source-ancestry symlink swap, captured-byte tampering and
redacted repr, unqualified pinned remote rejection, stale revision, imported
Goon change with identical root Plan and original execution prework rejection.
AG-01 preview regressions and the unchanged AG-00/X0 architecture test remain
required.
