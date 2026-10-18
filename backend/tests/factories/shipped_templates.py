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

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"


class CachedShippedPlanTemplateStore(PlanTemplateStore):
    """Cache immutable checked-in Plan reads for this store's lifetime."""

    def __init__(self, root: Path | None = None) -> None:
        super().__init__(root)
        self.catalog = cache(super().catalog)
        self.keys = cache(super().keys)
        self.read = cache(super().read)
        self.runnable_keys = cache(super().runnable_keys)
        self.source = cache(super().source)


class CachedShippedGoonTemplateStore(GoonTemplateStore):
    """Cache immutable checked-in Goon reads for this store's lifetime."""

    def __init__(
        self, root: Path | None = None, *, tool_authority_path: Path | None = None
    ) -> None:
        super().__init__(root, tool_authority_path=tool_authority_path)
        self.catalog = cache(super().catalog)
        self.keys = cache(super().keys)
        self.read = cache(super().read)
        self.source = cache(super().source)


PLAN_TEMPLATES = CachedShippedPlanTemplateStore(TEMPLATES / "plans")
GOON_TEMPLATES = CachedShippedGoonTemplateStore(TEMPLATES / "goons")
