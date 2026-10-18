from fastapi import APIRouter, Depends

from oh_goon.api.dependencies import migration_service
from oh_goon.core.models.migration import AdoptUvPreview
from oh_goon.services.migration import MigrationService

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("/adopt-uv", response_model=AdoptUvPreview, operation_id="adopt_uv_preview")
def adopt_uv_preview(
    service: MigrationService = Depends(migration_service),
) -> AdoptUvPreview:
    return service.preview()
