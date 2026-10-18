from __future__ import annotations

"""Explicit capability-ID invalidation groups used by current capability definitions.

Durable snapshots carry the resolved capability IDs, so historical freshness never
reinterprets action families through the current registry.
"""

INIT_CAPABILITIES = frozenset({"initialize"})
PLAN_CAPABILITIES = frozenset({"plan"})
UPDATE_CAPABILITIES = frozenset({"update"})
VERIFY_CAPABILITIES = frozenset({"verify"})
PREPARE_REVIEW_CAPABILITIES = frozenset({"prepare-review"})

INIT_INVALIDATES = PLAN_CAPABILITIES | UPDATE_CAPABILITIES | VERIFY_CAPABILITIES
UPDATE_INVALIDATES = INIT_CAPABILITIES | PLAN_CAPABILITIES | VERIFY_CAPABILITIES
REVIEW_INVALIDATES = INIT_CAPABILITIES | PLAN_CAPABILITIES | VERIFY_CAPABILITIES
ADOPT_UV_INVALIDATES = (
    INIT_CAPABILITIES
    | PLAN_CAPABILITIES
    | UPDATE_CAPABILITIES
    | VERIFY_CAPABILITIES
    | PREPARE_REVIEW_CAPABILITIES
)
