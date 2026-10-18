from fastapi import APIRouter, Depends

from oh_goon.api.dependencies import command_center_service
from oh_goon.core.models.command_center import FleetCommandCenter
from oh_goon.services.command_center import CommandCenterService

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("/recipes", response_model=FleetCommandCenter, operation_id="fleet_command_center")
def fleet_command_center(
    service: CommandCenterService = Depends(command_center_service),
) -> FleetCommandCenter:
    return service.status()
