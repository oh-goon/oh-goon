import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.vocabulary.result_semantics import FailureImpact
from oh_goon.integrations.fleet_plan_compilation import parse_plan_template


def test_plan_goon_import_failure_impact_defaults_blocking_and_accepts_non_blocking() -> None:
    compiled = parse_plan_template(
        """\nversion: 1\ngoons:\n- general/first\n- import: general/second\n  failure_impact: non_blocking\n"""
    )
    assert compiled.steps[0].failure_impact is FailureImpact.BLOCKING
    assert compiled.steps[1].failure_impact is FailureImpact.NON_BLOCKING


def test_plan_goon_import_rejects_unknown_failure_impact() -> None:
    with pytest.raises(FleetPlanInvalid, match="failure_impact"):
        parse_plan_template(
            """\nversion: 1\ngoons:\n- import: general/first\n  failure_impact: pretend_success\n"""
        )
