# ADR — LACE-WF-03B: one host-process backend, original command supervision

**Decision:** Implement the TL-1 host-process adapter under `ProcessIsolationBackend`, but do not register it as a deployable Fleet backend before the original Fleet Run owner transports interactive consent and supplies an effect-time authority check.

**Rationale:** A trusted developer should be able to run explicitly approved code on ordinary WSL/Linux without unrelated cgroup/namespace capabilities, but must not accidentally receive a strict sandbox claim or a Plan-selected trust downgrade. TL-1 is host-level trust, not containment.

**One owner chain:** Plan Resolver → Fleet/Enforcer admission → original LACE effect checks → exact bound backend → original `run_command` supervision → journal → Game Tape. The backend adds no selection or classification owner. The original process supervisor owns command lifecycle/cancel/output. The existing Fleet issuer must own all future effect authorizations; the typed `TrustedLocalEffectAuthority` port is **unimplemented by production**.

**Denied:** No consent, stale Run/Plan/backend/generation, worker mismatch, unattended, multi-repo, untrusted Plan, kernel caps, restricted egress, read-only workspace requiring real enforcement, original effect-authority denial, and any unregistered backend selection. Default strict selection is unaffected.

**Deferred:** A durable post-placement consent and issuer protocol, physical host qualification, real Iris/Goon execution, cancellation/recovery, and enabling an explicit separate executor inventory mode. Do not grant a missing port via a local fallback or boolean override.
