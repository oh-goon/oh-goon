# ADR — OH-Goon authority and external repository intelligence boundary

**Status:** Accepted (OH-Goon-owned decision). Hashmarks supplies required repository evidence; physical custody authority remains Oh-Goon-owned.

OH-Goon owns Run/Step meaning, signed admission, trust/revocation, physical custody and cleanup classification. The original LACE owner must independently establish native security-critical facts and enforce Run/Step physical containment. Enforcer alone makes admission, cleanup and terminal decisions; Game Tape projects authenticated journal facts without reclassifying them.

Hashmarks is the required repository-evidence producer, verified before application startup creates state. Model-backed Agent Steps consume its task evidence over their captured source. Its source/member/call-chain correspondence does not establish physical custody or decide native execution, LACE recovery, Enforcer admission or Gate A/B/C. External repository freshness does not imply runtime/process/FD/mount/cgroup freshness. No diagnostic record can become an authoritative physical absence or cleanup permit. Diagnostic integrations reuse existing read-only adapters; migrating original LACE or Enforcer effect ownership requires a separate explicit qualified architecture decision. The current handoff is defined in [Hashmarks execution layer](../development/HASHMARKS_EXECUTION_LAYER.md).

**Fail before effects:** Absent, unproven or revoked physical custody never becomes permission through local observations or an untrusted token. Preserve canonical owners and durable unresolved records rather than introducing fallback cleanup.
