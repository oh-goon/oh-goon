from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from oh_goon.api.dependencies import fleet_service
from oh_goon.core.game_tape_pi_session import fleet_snapshot_readback
from oh_goon.core.models.fleet import FleetSnapshot
from oh_goon.services.fleet import FleetService

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("", response_model=FleetSnapshot, operation_id="fleet_snapshot")
def snapshot(service: FleetService = Depends(fleet_service)) -> FleetSnapshot | JSONResponse:
    observed = service.snapshot()
    public = fleet_snapshot_readback(observed)
    return JSONResponse(public) if public is not None else observed
