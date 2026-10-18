from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from oh_goon.api.dependencies import executor_pool
from oh_goon.core.models.fleet_executor_operations import (
    FleetExecutorPoolSnapshot,
    FleetExecutorStatus,
    PlacementQueueEntry,
    PlacementQueueSnapshot,
)
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.runtime.executor_pool import FleetExecutorPool

router = APIRouter(prefix="/fleet/executors", tags=["fleet"])


class QueueCancelRequest(BaseModel):
    expected_sequence: int = Field(ge=1)


class QueueReprioritizeRequest(BaseModel):
    admitted_priority: SchedulingPriority
    expected_scheduling_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=240)


class ExecutorControlRequest(BaseModel):
    expected_generation: int = Field(ge=1)
    expected_operational_revision: int = Field(ge=0)


@router.get("", response_model=FleetExecutorPoolSnapshot, operation_id="fleet_executor_status")
def executor_status(pool: FleetExecutorPool = Depends(executor_pool)) -> FleetExecutorPoolSnapshot:
    return pool.executor_snapshot()


@router.get(
    "/placement-queue",
    response_model=PlacementQueueSnapshot,
    operation_id="fleet_placement_queue",
)
def placement_queue(pool: FleetExecutorPool = Depends(executor_pool)) -> PlacementQueueSnapshot:
    return pool.placement_queue()


@router.post(
    "/placement-queue/{fleet_run_id}/cancel",
    response_model=PlacementQueueEntry,
    status_code=202,
    operation_id="cancel_queued_admission",
)
def cancel_queued_admission(
    fleet_run_id: str,
    request: QueueCancelRequest,
    pool: FleetExecutorPool = Depends(executor_pool),
) -> PlacementQueueEntry:
    return pool.cancel_queued_admission(fleet_run_id, expected_sequence=request.expected_sequence)


@router.post(
    "/placement-queue/{fleet_run_id}/priority",
    response_model=PlacementQueueEntry,
    operation_id="reprioritize_queued_admission",
)
def reprioritize_queued_admission(
    fleet_run_id: str,
    request: QueueReprioritizeRequest,
    pool: FleetExecutorPool = Depends(executor_pool),
) -> PlacementQueueEntry:
    return pool.reprioritize_queued_admission(
        fleet_run_id,
        admitted_priority=request.admitted_priority,
        expected_revision=request.expected_scheduling_revision,
        reason=request.reason,
    )


@router.post(
    "/{executor_id}/drain",
    response_model=FleetExecutorStatus,
    operation_id="drain_executor",
)
def drain_executor(
    executor_id: str,
    request: ExecutorControlRequest,
    pool: FleetExecutorPool = Depends(executor_pool),
) -> FleetExecutorStatus:
    return pool.drain_executor(
        executor_id,
        expected_generation=request.expected_generation,
        expected_revision=request.expected_operational_revision,
    )


@router.post(
    "/{executor_id}/activate",
    response_model=FleetExecutorStatus,
    operation_id="activate_executor",
)
def activate_executor(
    executor_id: str,
    request: ExecutorControlRequest,
    pool: FleetExecutorPool = Depends(executor_pool),
) -> FleetExecutorStatus:
    return pool.activate_executor(
        executor_id,
        expected_generation=request.expected_generation,
        expected_revision=request.expected_operational_revision,
    )


@router.post(
    "/{executor_id}/trust/revoke",
    response_model=FleetExecutorStatus,
    operation_id="revoke_executor_trust",
)
def revoke_executor_trust(
    executor_id: str,
    request: ExecutorControlRequest,
    pool: FleetExecutorPool = Depends(executor_pool),
) -> FleetExecutorStatus:
    return pool.revoke_executor_trust(
        executor_id,
        expected_generation=request.expected_generation,
        expected_revision=request.expected_operational_revision,
    )


@router.post(
    "/{executor_id}/trust/restore",
    response_model=FleetExecutorStatus,
    operation_id="restore_executor_trust",
)
def restore_executor_trust(
    executor_id: str,
    request: ExecutorControlRequest,
    pool: FleetExecutorPool = Depends(executor_pool),
) -> FleetExecutorStatus:
    return pool.restore_executor_trust(
        executor_id,
        expected_generation=request.expected_generation,
        expected_revision=request.expected_operational_revision,
    )
