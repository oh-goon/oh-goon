from fastapi import APIRouter, Depends

from oh_goon.api.dependencies import system_health_service
from oh_goon.core.models.tool_health import SystemHealth
from oh_goon.services.system_health import SystemHealthService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=SystemHealth, operation_id="health")
def health(service: SystemHealthService = Depends(system_health_service)) -> SystemHealth:
    return service.inspect()
