from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from oh_goon.core.models.enforcer_receipt import EnforcerRunReceipt
from oh_goon.services.enforcer_run_receipt import enforcer_run_receipt, render_enforcer_yaml
from tests.unit.services.test_enforcer_run_receipt import _detail


def test_enforcer_receipt_v17_groups_privacy_safe_resolved_plan_shape(tmp_path: Path) -> None:
    detail = _detail(tmp_path)
    receipt = enforcer_run_receipt(detail)
    payload = yaml.safe_load(render_enforcer_yaml(receipt))
    plan = payload["plan"]

    assert plan["plan_id"] == detail.run.target.plan.plan_id
    assert plan["contract_version"] == detail.run.target.plan.contract_version
    assert plan["name"] == detail.run.target.plan.name
    assert plan["execution"] == detail.run.target.plan.execution.model_dump(mode="json")
    assert plan["requirements"] == [
        item.model_dump(mode="json") for item in detail.run.target.plan.requirements
    ]
    assert plan["imports"][0]["import_id"] == "quality"
    assert plan["steps"][0]["step_id"] == "tests"
    assert plan["steps"][0]["required_tool"] == "uv"
    for removed in (
        "plan_id",
        "plan_contract_version",
        "plan_name",
        "plan_execution",
        "requirements",
        "imports",
        "required_tools",
        "resolved_steps",
    ):
        assert removed not in payload
    assert "argv" not in plan["steps"][0]
    assert "working_directory" not in plan["steps"][0]


def test_enforcer_receipt_v17_rejects_flat_resolved_plan_fields(tmp_path: Path) -> None:
    payload = enforcer_run_receipt(_detail(tmp_path)).model_dump(
        mode="json", exclude_computed_fields=True
    )
    plan = payload.pop("plan")
    payload.update(
        plan_id=plan["plan_id"],
        plan_contract_version=plan["contract_version"],
        plan_name=plan["name"],
        plan_execution=plan["execution"],
        requirements=plan["requirements"],
        imports=plan["imports"],
        resolved_steps=plan["steps"],
    )

    with pytest.raises(ValidationError):
        EnforcerRunReceipt.model_validate(payload)


def test_enforcer_receipt_v18_reuses_safe_resolved_step_policies(tmp_path: Path) -> None:
    payload = yaml.safe_load(render_enforcer_yaml(enforcer_run_receipt(_detail(tmp_path))))
    step = payload["plan"]["steps"][0]

    assert step["workspace"] == {"source": {"kind": "repository"}, "publish": False}
    assert step["cache"] == {"source": {"kind": "empty"}, "publish": False, "retain": False}
    assert step["retry"]["max_attempts"] >= 1
    assert "egress_mode" in step
    for removed in (
        "workspace_source",
        "workspace_publish",
        "cache_source",
        "cache_publish",
        "cache_retain",
        "retry_max_attempts",
    ):
        assert removed not in step


def test_enforcer_receipt_v18_rejects_flat_resolved_step_policy_fields(tmp_path: Path) -> None:
    payload = enforcer_run_receipt(_detail(tmp_path)).model_dump(
        mode="json", exclude_computed_fields=True
    )
    step = payload["plan"]["steps"][0]
    workspace = step.pop("workspace")
    cache = step.pop("cache")
    retry = step.pop("retry")
    step.update(
        workspace_source=workspace["source"]["kind"],
        workspace_publish=workspace["publish"],
        cache_source=cache["source"]["kind"],
        cache_publish=cache["publish"],
        cache_retain=cache["retain"],
        retry_max_attempts=retry["max_attempts"],
    )

    with pytest.raises(ValidationError):
        EnforcerRunReceipt.model_validate(payload)
