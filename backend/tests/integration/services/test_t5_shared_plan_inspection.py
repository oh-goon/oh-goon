"""Real source-cut T5 inspection, no effect or second parse."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInputRejected, FleetPlanSourceStale
from oh_goon.runtime.shared_plans import SharedPlanFileStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.shared_plans import SharedPlanService
from tests.factories.shipped_templates import GOON_TEMPLATES


def test_inspection_resolves_exact_source_once_without_lock_run_or_admission(tmp_path):
    store = SharedPlanFileStore(tmp_path / "plans")
    document = store.write("quality", "version: 1\ninputs: [coverage_threshold]\ngoons: [python/quality]\n")
    resolver = FleetPlanResolver(goon_templates=GOON_TEMPLATES)
    plans = FleetPlanService(resolver, Mock(), Mock(), Mock(), Mock())
    original = plans.resolve_with_source
    plans.resolve_with_source = Mock(wraps=original)
    league = Mock()
    runs = Mock()
    service = SharedPlanService(store, league, plans, runs, Mock())
    plan_key, revision, plan_id = service.inspect_inputs("quality", inputs={"coverage_threshold": "85"}, expected_revision=document.sha256)
    assert plan_key == "quality" and revision == document.sha256 and plan_id
    plans.resolve_with_source.assert_called_once()
    runs.assert_not_called()
    league.assert_not_called()
    assert not (store.path("quality").parent / "goon.lock").exists()
    with pytest.raises(FleetPlanInputRejected) as error:
        service.inspect_inputs("quality", inputs={"coverage_threshold": "101"}, expected_revision=document.sha256)
    assert error.value.diagnostic_code == "INVALID_INPUT_VALUE"
    store.write("quality", "version: 1\ninputs: [coverage_threshold]\ngoons: [python/quality]\n# new\n", expected_revision=document.sha256)
    with pytest.raises(FleetPlanSourceStale):
        service.inspect_inputs("quality", inputs={"coverage_threshold": "85"}, expected_revision=document.sha256)
