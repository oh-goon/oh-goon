# ADR — OH-Goon authority and external repository intelligence boundary

**Status:** Accepted (OH-Goon-owned decision). **No external implementation dependency.**

OH-Goon owns Run/Step meaning, signed admission, trust/revocation, physical custody and cleanup classification. The original LACE owner must independently establish native security-critical facts and enforce Run/Step physical containment. Enforcer alone makes admission, cleanup and terminal decisions; Game Tape projects authenticated journal facts without reclassifying them.

Repository-intelligence tools such as Hashmarks may provide non-authoritative source/member/call-chain correspondence, but no such tool is required for native execution, LACE recovery, Enforcer admission, Gate A/B/C, or this checkpoint. External repository freshness does not imply runtime/process/FD/mount/cgroup freshness. No diagnostic record can become an authoritative physical absence or cleanup permit. Any optional future diagnostic integration must reuse existing read-only adapters; migrating original LACE or Enforcer effect ownership requires a separate explicit qualified architecture decision.

**Fail before effects:** Absent, unproven or revoked physical custody never becomes permission through local observations or an untrusted token. Preserve canonical owners and durable unresolved records rather than introducing fallback cleanup.
