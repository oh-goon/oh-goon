from __future__ import annotations

from types import SimpleNamespace

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.services import shared_plan_preview_diagnostics as subject


def _team(*cache_sources: str):
    return SimpleNamespace(
        preview=SimpleNamespace(steps=tuple(SimpleNamespace(cache_source=s) for s in cache_sources))
    )


def test_preview_readiness_does_not_claim_host_storage_or_cache_proof() -> None:
    facts = subject.preview_readiness((_team("empty"),))
    assert [fact.reason_code for fact in facts] == [
        "HOST_STORAGE_NOT_PROBED_IN_PREVIEW",
        "CACHE_REUSE_NOT_REQUESTED",
    ]
    assert facts[0].state == "NOT_PROBED"


def test_retained_cache_preview_remains_runtime_bound_without_rehash() -> None:
    facts = subject.preview_readiness((_team("retained:ref-123"),))
    assert facts[1].reason_code == "CACHE_REUSE_RUNTIME_BOUND"


def test_blocked_preview_diagnostic_uses_existing_domain_reason_code() -> None:
    diagnostic = subject.blocked_preview_diagnostic(FleetPlanInvalid("Dockerfile is missing"))
    assert diagnostic.authority_owner == "fleet_plan_preview"
    assert diagnostic.reason_code == "FLEET_PLAN_INVALID"
    assert diagnostic.state == "BLOCKED"
