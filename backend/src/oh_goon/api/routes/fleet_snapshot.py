from fastapi import APIRouter, Depends

from oh_goon.api.dependencies import fleet_service
from oh_goon.core.models.fleet import FleetSnapshot
from oh_goon.services.fleet import FleetService

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("", response_model=FleetSnapshot, operation_id="fleet_snapshot")
def snapshot(service: FleetService = Depends(fleet_service)) -> FleetSnapshot:
    return service.snapshot()
