# ADR — CO-AG5: an exit status is not a second semantic owner

**Decision:** For `plan diff`, the existing `FleetPlanResolver.diff` -> `semantic_plan_diff` -> `FleetPlanSemanticDiff.changed` remains the only authority deciding whether resolved execution meaning changed. The CLI may present that existing boolean through an optional `--exit-code` (0 unchanged, 1 changed; 2 invalid). Without the flag, the historical exit code remains 0 for all valid comparisons.

**Reasons:** Agents and CI can gate changed execution intent without parsing prose or adding their own interpretation of Plan IDs. An optional flag avoids changing existing callers; JSON/human payloads remain byte/schema-compatible. A relocated template can legitimately change implicit Plan naming, so byte-comparison shortcuts would be incorrect.

**Exclusions:** No new semantic diff implementation, persisted comparison store, change policy, approval/Run admission, lock mutation, Hashmarks code, physical LACE effect or signer. Shared and scheduled Plan authority remains with Fleet owners, not the diagnostic.

**Tests:** Resolve before/after through the original service, compare JSON/text unchanged, exercise positive/negative/default/error statuses, reject before runtime construction, and verify no lock/database write. Release and native gates stay separately BLOCKED.
