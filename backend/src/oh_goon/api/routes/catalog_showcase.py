from fastapi import APIRouter, Depends

from oh_goon.api.dependencies import catalog_showcase_service
from oh_goon.api.schemas.catalog_showcase import (
    CatalogExploreRequest,
    CatalogPreviewRequest,
    CatalogRunRequest,
)
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import StateConflict
from oh_goon.core.models.catalog_showcase import CatalogExploration, CatalogPreview
from oh_goon.core.models.run import FleetRunState
from oh_goon.services.catalog_showcase import CatalogShowcaseService

router = APIRouter(prefix="/repositories/{repository_id}/catalog", tags=["catalog"])


@router.post("/explore", response_model=CatalogExploration, operation_id="catalog_explore")
def catalog_explore(
    repository_id: str,
    request: CatalogExploreRequest,
    service: CatalogShowcaseService = Depends(catalog_showcase_service),
) -> CatalogExploration:
    return service.explore(repository_id, **request.model_dump())


@router.post("/previews", response_model=CatalogPreview, operation_id="catalog_preview")
def catalog_preview(
    repository_id: str,
    request: CatalogPreviewRequest,
    service: CatalogShowcaseService = Depends(catalog_showcase_service),
) -> CatalogPreview:
    return service.preview(
        repository_id, source_authority=request.source_authority, candidate=request.candidate
    )


@router.post("/runs", response_model=FleetRunState, status_code=202, operation_id="catalog_run")
def catalog_run(
    repository_id: str,
    request: CatalogRunRequest,
    service: CatalogShowcaseService = Depends(catalog_showcase_service),
) -> FleetRunState:
    try:
        return service.run(
            repository_id,
            source_authority=request.source_authority,
            candidate=request.candidate,
            expected_plan_id=request.expected_plan_id,
            expected_plan_sha256=request.expected_plan_sha256,
            expected_execution_digest=request.expected_execution_digest,
            fleet_run_id=request.fleet_run_id,
        )
    except TrustProtocolError as exc:
        raise StateConflict(
            "Fleet Plan execution requires valid authenticated admission authority"
        ) from exc
