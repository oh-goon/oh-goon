from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oh_goon.api import dependencies
from oh_goon.api.routes import fleet_runs
from oh_goon.core.models.fleet_plan_evidence import RequirementsRepositoryAdmissionEvidence
from oh_goon.core.models.run import FleetRunDetail
from tests.unit.services.test_enforcer_run_receipt import _detail


def _zero_requirement_detail(tmp_path: Path) -> FleetRunDetail:
    detail = _detail(tmp_path)
    report = detail.operations[0]
    result = report.evidence.plan
    execution_inputs = result.execution_inputs
    assert execution_inputs is not None
    updated_inputs = execution_inputs.model_copy(
        update={
            "repository_admission": RequirementsRepositoryAdmissionEvidence(
                requirements_id="requirements-boundary",
                requirement_count=0,
            )
        }
    )
    updated_result = result.model_copy(update={"execution_inputs": updated_inputs})
    updated_report = report.model_copy(
        update={"evidence": report.evidence.model_copy(update={"plan": updated_result})}
    )
    return detail.model_copy(update={"operations": (updated_report,)})


def test_zero_requirement_strict_run_serializes_detail_and_enforcer_receipts(
    tmp_path: Path,
) -> None:
    detail = _zero_requirement_detail(tmp_path)
    detail_payload = detail.model_dump(mode="json", exclude={"game_tape"})
    assert (
        detail_payload["plan_trust_receipts"][0]["receipt"]["admission"]["requirement_count"] == 0
    )

    class Manager:
        def get(self, fleet_run_id: str) -> FleetRunDetail:
            assert fleet_run_id == detail.run.fleet_run_id
            return detail

    app = FastAPI()
    app.include_router(fleet_runs.router, prefix="/api")
    app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: Manager()
    response = TestClient(app).get(f"/api/fleet/runs/{detail.run.fleet_run_id}/enforcer.yml")

    assert response.status_code == 200
    payload = yaml.safe_load(response.text)
    assert payload["repositories"][0]["execution_input"]["admission"]["requirement_count"] == 0
