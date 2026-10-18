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
    """Cache immutable checked-in Plan reads for the lifetime of one pytest process."""

    @cache
    def catalog(self):
        return super().catalog()

    @cache
    def keys(self):
        return super().keys()

    @cache
    def read(self, template_key: str):
        return super().read(template_key)

    @cache
    def runnable_keys(self):
        return super().runnable_keys()

    @cache
    def source(self, template_key: str):
        return super().source(template_key)


class CachedShippedGoonTemplateStore(GoonTemplateStore):
    """Cache immutable checked-in Goon reads for the lifetime of one pytest process."""

    @cache
    def catalog(self):
        return super().catalog()

    @cache
    def keys(self):
        return super().keys()

    @cache
    def read(self, template_key: str):
        return super().read(template_key)

    @cache
    def source(self, template_key: str):
        return super().source(template_key)


PLAN_TEMPLATES = CachedShippedPlanTemplateStore(TEMPLATES / "plans")
GOON_TEMPLATES = CachedShippedGoonTemplateStore(TEMPLATES / "goons")
