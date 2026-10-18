from fastapi import APIRouter, Depends

from oh_goon.api.dependencies import security_service
from oh_goon.core.models.security import FleetSbomBundle, FleetSecuritySummary
from oh_goon.services.security import SecurityService

router = APIRouter(prefix="/fleet/security", tags=["fleet"])


@router.get("", response_model=FleetSecuritySummary, operation_id="fleet_security")
def fleet_security(
    service: SecurityService = Depends(security_service),
) -> FleetSecuritySummary:
    return service.summary()


@router.get("/sbom/{fleet_run_id}", response_model=FleetSbomBundle, operation_id="fleet_sbom")
def fleet_sbom(
    fleet_run_id: str,
    service: SecurityService = Depends(security_service),
) -> FleetSbomBundle:
    return service.sbom_bundle(fleet_run_id)
