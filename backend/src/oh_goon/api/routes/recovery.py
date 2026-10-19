from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from oh_goon.api.dependencies import recovery_service
from oh_goon.core.models.recovery_status import RecoveryStatus
from oh_goon.services.recovery import RecoveryService

router = APIRouter(prefix="/recovery", tags=["recovery"])


class RecoveryReconcileRequest(BaseModel):
    expected_record_version: int = Field(ge=0)


class RecoveryAcknowledgeRequest(BaseModel):
    note: str = Field(default="Inspected manually", min_length=1, max_length=500)
    expected_record_version: int = Field(ge=0)


@router.get("", response_model=RecoveryStatus, operation_id="recovery_status")
def recovery_status(service: RecoveryService = Depends(recovery_service)) -> RecoveryStatus:
    return service.status()


@router.post(
    "/effects/{operation_id}/reconcile",
    response_model=RecoveryStatus,
    operation_id="reconcile_effect",
)
def reconcile_effect(
    operation_id: str,
    request: RecoveryReconcileRequest,
    service: RecoveryService = Depends(recovery_service),
) -> RecoveryStatus:
    return service.reconcile(operation_id, expected_record_version=request.expected_record_version)


@router.post(
    "/effects/{operation_id}/acknowledge",
    response_model=RecoveryStatus,
    operation_id="acknowledge_effect",
)
def acknowledge_effect(
    operation_id: str,
    request: RecoveryAcknowledgeRequest,
    service: RecoveryService = Depends(recovery_service),
) -> RecoveryStatus:
    return service.acknowledge(
        operation_id,
        note=request.note,
        expected_record_version=request.expected_record_version,
    )
