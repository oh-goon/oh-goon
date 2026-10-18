from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from oh_goon.core.models.enforcer_receipt import EnforcerRunReceipt
from oh_goon.services.enforcer_run_receipt import enforcer_run_receipt, render_enforcer_yaml
from tests.unit.services.test_enforcer_run_receipt import _detail


def test_enforcer_receipt_v17_groups_structural_run_lifecycle(tmp_path: Path) -> None:
    receipt = enforcer_run_receipt(_detail(tmp_path))
    payload = yaml.safe_load(render_enforcer_yaml(receipt))

    assert payload["lifecycle"]["status"] == "COMPLETED"
    assert payload["lifecycle"]["accepted_at"] == payload["lifecycle"]["started_at"]
    assert payload["lifecycle"]["started_at"] == payload["lifecycle"]["finished_at"]
    assert payload["outcome"] == "SUCCESS"
    for removed in ("accepted_at", "started_at", "finished_at", "run_status", "run_outcome"):
        assert removed not in payload


def test_enforcer_receipt_v17_rejects_flat_run_lifecycle(tmp_path: Path) -> None:
    payload = enforcer_run_receipt(_detail(tmp_path)).model_dump(
        mode="json", exclude_computed_fields=True
    )
    lifecycle = payload.pop("lifecycle")
    outcome = payload.pop("outcome")
    payload.update(
        accepted_at=lifecycle["accepted_at"],
        started_at=lifecycle["started_at"],
        finished_at=lifecycle["finished_at"],
        run_status=lifecycle["status"],
        run_outcome=outcome,
    )

    with pytest.raises(ValidationError):
        EnforcerRunReceipt.model_validate(payload)


def test_enforcer_receipt_v17_rejects_impossible_run_lifecycle(tmp_path: Path) -> None:
    payload = enforcer_run_receipt(_detail(tmp_path)).model_dump(
        mode="json", exclude_computed_fields=True
    )
    payload["lifecycle"]["status"] = "RUNNING"

    with pytest.raises(ValidationError, match="Invalid lifecycle timestamps for RUNNING"):
        EnforcerRunReceipt.model_validate(payload)
