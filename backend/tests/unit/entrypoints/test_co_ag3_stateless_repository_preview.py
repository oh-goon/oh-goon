"""CO-AG3: repository-scoped Plan preview reuses original read-only evaluation."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService


def _source(key: str = "python/dependency-inventory") -> Path:
    return PlanTemplateStore().source(key).path


def _resolver() -> FleetPlanResolver:
    return FleetPlanResolver(goon_templates=GoonTemplateStore())


def test_original_service_preview_delegates_to_one_canonical_resolver(tmp_path: Path) -> None:
    resolver = _resolver()
    service = FleetPlanService(resolver, None, None, None, None)
    source = _source()
    assert service.preview(source, tmp_path) == resolver.preview(source, tmp_path)


def test_original_service_preview_delegates_once_without_second_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _resolver()
    service = FleetPlanService(resolver, None, None, None, None)
    expected = resolver.preview(_source(), tmp_path)
    seen: list[tuple[Path, Path]] = []

    def original_owner(plan_path, repository, *, extensions_path=None, inputs=None):
        seen.append((plan_path, repository))
        return expected

    monkeypatch.setattr(resolver, "preview", original_owner)
    monkeypatch.setattr(service, "resolve", lambda *_a, **_kw: pytest.fail("duplicate resolver"))
    assert service.preview(_source(), tmp_path) == expected
    assert seen == [(_source(), tmp_path)]
