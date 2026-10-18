from fastapi import APIRouter, Depends

from oh_goon.api.dependencies import repository_service
from oh_goon.api.schemas.repositories import (
    ExcludeRepositoryRequest,
    ExclusionRecord,
    IncludeRepositoryRequest,
)
from oh_goon.core.models.repository import (
    ExcludedRepositoryCatalogEntry,
    IncludedRepositoryCatalogEntry,
    RepositoryCatalogEntry,
)
from oh_goon.services.repositories import RepositoryService

router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("", response_model=list[RepositoryCatalogEntry], operation_id="repository_catalog")
def repositories(
    service: RepositoryService = Depends(repository_service),
) -> list[RepositoryCatalogEntry]:
    return service.list()


@router.get(
    "/exclusions",
    response_model=list[ExclusionRecord],
    operation_id="repository_exclusions",
)
def exclusions(service: RepositoryService = Depends(repository_service)) -> list[dict[str, str]]:
    return service.exclusions()


@router.post(
    "/exclude", response_model=ExcludedRepositoryCatalogEntry, operation_id="exclude_repository"
)
def exclude_repository(
    request: ExcludeRepositoryRequest,
    service: RepositoryService = Depends(repository_service),
) -> ExcludedRepositoryCatalogEntry:
    return service.exclude(
        request.repository, request.reason, request.expected_repository_instance_id
    )


@router.post(
    "/include", response_model=IncludedRepositoryCatalogEntry, operation_id="include_repository"
)
def include_repository(
    request: IncludeRepositoryRequest,
    service: RepositoryService = Depends(repository_service),
) -> IncludedRepositoryCatalogEntry:
    return service.include(request.repository, request.expected_repository_instance_id)
