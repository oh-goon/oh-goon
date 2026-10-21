"""Process-local caches for immutable shipped template fixtures.

These helpers are test-only. They deliberately cache only checked-in Plan/Goon packages;
tests that mutate temporary template trees must construct normal stores so freshness and
package-shape validation remain observable.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"


class CachedShippedPlanTemplateStore(PlanTemplateStore):
    """Cache immutable checked-in Plan reads for this store's lifetime."""

    def __init__(self, root: Path | None = None) -> None:
        super().__init__(root)
        self.__dict__.update(
            catalog=cache(super().catalog),
            keys=cache(super().keys),
            read=cache(super().read),
            runnable_keys=cache(super().runnable_keys),
            source=cache(super().source),
        )


class CachedShippedGoonTemplateStore(GoonTemplateStore):
    """Cache immutable checked-in Goon reads for this store's lifetime."""

    def __init__(
        self, root: Path | None = None, *, tool_authority_path: Path | None = None
    ) -> None:
        super().__init__(root, tool_authority_path=tool_authority_path)
        self.__dict__.update(
            catalog=cache(super().catalog),
            keys=cache(super().keys),
            read=cache(super().read),
            source=cache(super().source),
        )


PLAN_TEMPLATES = CachedShippedPlanTemplateStore(TEMPLATES / "plans")
GOON_TEMPLATES = CachedShippedGoonTemplateStore(TEMPLATES / "goons")

# Agent Plans use captured sources and standard-local admission. Their native
# lifecycle proofs are parametrized over this same compiler-owned membership.
AGENT_PLAN_TEMPLATES = tuple(
    key
    for key in PLAN_TEMPLATES.keys()
    if FleetPlanResolver(goon_templates=GOON_TEMPLATES)
    .analyze_source(PLAN_TEMPLATES.source(key).path)
    .agent_step_sources()
)
