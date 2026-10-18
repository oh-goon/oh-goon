from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from oh_goon.api import dependencies
from oh_goon.api.routes import fleet_executors
from oh_goon.core.models.fleet_executor_operations import (
    CancellationRequestedPlacementQueueEntry,
    ExecutorAdmissionMode,
    ExecutorTrustState,
    FleetExecutorPoolSnapshot,
    FleetExecutorStatus,
    PlacementQueueEntry,
    PlacementQueueSnapshot,
    WaitingPlacementQueueEntry,
)


class _OperatorPool:
    def __init__(self) -> None:
        self.mode = ExecutorAdmissionMode.ACTIVE
        self.trust = ExecutorTrustState.TRUSTED
        self.cancelled: list[str] = []

    def _status(self) -> FleetExecutorStatus:
        return FleetExecutorStatus(
            executor_id="executor-a",
            generation=7,
            priority=10,
            backend_id="opensandbox",
            admission_mode=self.mode,
            trust_state=self.trust,
            active_fleet_run_ids=("run-bound",),
            operational_revision=2,
        )

    def executor_snapshot(self) -> FleetExecutorPoolSnapshot:
        return FleetExecutorPoolSnapshot(executors=(self._status(),))

    def placement_queue(self) -> PlacementQueueSnapshot:
        return PlacementQueueSnapshot(
            entries=(
                WaitingPlacementQueueEntry(
                    state="waiting", fleet_run_id="run-waiting", sequence=11, position=1,
                    priority_decision_reason="LOCAL_V1_AUTHORIZED",
                    scheduling_policy_revision="local-v1",
                    priority_policy_revision="local-v1",
                ),
            )
        )

    def cancel_queued_admission(self, fleet_run_id: str, *, expected_sequence: int) -> PlacementQueueEntry:
        assert expected_sequence == 11
        self.cancelled.append(fleet_run_id)
        return CancellationRequestedPlacementQueueEntry(
            state="cancellation_requested",
            fleet_run_id=fleet_run_id,
            sequence=11,
            position=1,
            priority_decision_reason="LOCAL_V1_AUTHORIZED",
            scheduling_policy_revision="local-v1",
            priority_policy_revision="local-v1",
        )

    def drain_executor(self, executor_id: str, *, expected_generation: int, expected_revision: int) -> FleetExecutorStatus:
        assert executor_id == "executor-a"
        assert expected_generation == 7
        assert expected_revision == 2
        self.mode = ExecutorAdmissionMode.DRAINING
        return self._status()

    def activate_executor(self, executor_id: str, *, expected_generation: int, expected_revision: int) -> FleetExecutorStatus:
        assert executor_id == "executor-a"
        assert expected_generation == 7
        assert expected_revision == 2
        self.mode = ExecutorAdmissionMode.ACTIVE
        return self._status()

    def revoke_executor_trust(self, executor_id: str, *, expected_generation: int, expected_revision: int) -> FleetExecutorStatus:
        assert executor_id == "executor-a"
        assert expected_generation == 7
        assert expected_revision == 2
        self.trust = ExecutorTrustState.REVOKED
        return self._status()

    def restore_executor_trust(self, executor_id: str, *, expected_generation: int, expected_revision: int) -> FleetExecutorStatus:
        assert executor_id == "executor-a"
        assert expected_generation == 7
        assert expected_revision == 2
        self.trust = ExecutorTrustState.TRUSTED
        return self._status()


def _client(pool: _OperatorPool) -> TestClient:
    app = FastAPI()
    app.include_router(fleet_executors.router, prefix="/api")
    app.dependency_overrides[dependencies.executor_pool] = lambda: pool
    return TestClient(app)


def test_executor_operator_api_exposes_queue_and_explicit_control_actions() -> None:
    pool = _OperatorPool()
    client = _client(pool)

    status = client.get("/api/fleet/executors")
    assert status.status_code == 200
    assert status.json()["executors"][0]["active_fleet_run_ids"] == ["run-bound"]

    queue = client.get("/api/fleet/executors/placement-queue")
    assert queue.status_code == 200
    assert queue.json()["entries"][0]["position"] == 1

    cancelled = client.post("/api/fleet/executors/placement-queue/run-waiting/cancel", json={"expected_sequence": 11})
    assert cancelled.status_code == 202
    assert cancelled.json()["state"] == "cancellation_requested"
    assert pool.cancelled == ["run-waiting"]

    drained = client.post("/api/fleet/executors/executor-a/drain", json={"expected_generation": 7, "expected_operational_revision": 2}).json()
    assert drained["admission_mode"] == "draining"
    assert drained["accepting_new_runs"] is False

    revoked = client.post("/api/fleet/executors/executor-a/trust/revoke", json={"expected_generation": 7, "expected_operational_revision": 2}).json()
    assert revoked["trust_state"] == "revoked"
    assert revoked["accepting_new_runs"] is False

    activated = client.post("/api/fleet/executors/executor-a/activate", json={"expected_generation": 7, "expected_operational_revision": 2}).json()
    assert activated["admission_mode"] == "active"
    assert activated["accepting_new_runs"] is False

    restored = client.post("/api/fleet/executors/executor-a/trust/restore", json={"expected_generation": 7, "expected_operational_revision": 2}).json()
    assert restored["trust_state"] == "trusted"
    assert restored["accepting_new_runs"] is True
