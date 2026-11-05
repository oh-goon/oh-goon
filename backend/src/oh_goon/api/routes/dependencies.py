from fastapi import APIRouter, Depends, Query
from starlette.convertors import PathConvertor, register_url_convertor

from oh_goon.api.dependencies import dependency_fleet_service
from oh_goon.core.dependency_identity import DependencyEcosystem
from oh_goon.core.models.dependency_fleet import (
    DependencyAdvisoryOverview,
    DependencyCoveragePopulation,
    DependencyCoverageRepositoryPage,
    DependencyEvidenceFilter,
    DependencyFleetCoverageOverview,
    DependencyPackageOverview,
    DependencyRepositoryPage,
    DependencySearchResult,
    DependencyTargetMaterialization,
    DependencyTargetMaterializeRequest,
    RepositoryDependencyPage,
)
from oh_goon.services.dependencies import DependencyFleetService


class _DependencyPackagePath(PathConvertor):
    """Transport one ordinary or scoped identifier; the producer owns its meaning."""

    regex = r"(?:@[^/]+/[^/]+|[^/@][^/]*)"


register_url_convertor("dependency_package", _DependencyPackagePath())
router = APIRouter(prefix="/dependencies", tags=["dependencies"])


@router.get("/search", response_model=DependencySearchResult, operation_id="dependency_search")
def dependency_search(
    q: str = Query(min_length=1, max_length=4096),
    ecosystem: DependencyEcosystem | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    service: DependencyFleetService = Depends(dependency_fleet_service),
) -> DependencySearchResult:
    return service.search(q, ecosystem=ecosystem, offset=offset, limit=limit)


@router.get(
    "/coverage",
    response_model=DependencyFleetCoverageOverview,
    operation_id="dependency_fleet_coverage",
)
def dependency_fleet_coverage(
    ecosystem: DependencyEcosystem = Query(default="pypi"),
    service: DependencyFleetService = Depends(dependency_fleet_service),
) -> DependencyFleetCoverageOverview:
    return service.fleet_coverage(ecosystem=ecosystem)


@router.get(
    "/repositories/{repository_id}",
    response_model=RepositoryDependencyPage,
    operation_id="repository_dependencies",
)
def repository_dependencies(
    repository_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    service: DependencyFleetService = Depends(dependency_fleet_service),
) -> RepositoryDependencyPage:
    return service.repository(repository_id, offset=offset, limit=limit)


@router.post(
    "/targets/materialize",
    response_model=DependencyTargetMaterialization,
    operation_id="materialize_dependency_targets",
)
def materialize_dependency_targets(
    request: DependencyTargetMaterializeRequest,
    service: DependencyFleetService = Depends(dependency_fleet_service),
) -> DependencyTargetMaterialization:
    return service.materialize(request)


@router.get(
    "/targets/{query_id}",
    response_model=DependencyTargetMaterialization,
    operation_id="dependency_target_selection",
)
def dependency_target_selection(
    query_id: str,
    service: DependencyFleetService = Depends(dependency_fleet_service),
) -> DependencyTargetMaterialization:
    return service.target_selection(query_id)


@router.get(
    "/{package_name:dependency_package}/coverage-repositories",
    response_model=DependencyCoverageRepositoryPage,
    operation_id="dependency_coverage_repositories",
)
def dependency_coverage_repositories(
    package_name: str,
    coverage: DependencyCoveragePopulation = Query(),
    ecosystem: DependencyEcosystem = Query(default="pypi"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    service: DependencyFleetService = Depends(dependency_fleet_service),
) -> DependencyCoverageRepositoryPage:
    return service.coverage_repositories(
        package_name, ecosystem=ecosystem, coverage=coverage, offset=offset, limit=limit
    )


@router.get(
    "/{package_name:dependency_package}/repositories",
    response_model=DependencyRepositoryPage,
    operation_id="dependency_repositories",
)
def dependency_repositories(
    package_name: str,
    ecosystem: DependencyEcosystem = Query(default="pypi"),
    resolved_version: str | None = Query(default=None, min_length=1, max_length=4096),
    version_prefix: str | None = Query(default=None, min_length=1, max_length=4096),
    version_specifier: str | None = Query(default=None, min_length=1, max_length=4096),
    advisory_id: str | None = Query(default=None, min_length=1, max_length=200),
    evidence: DependencyEvidenceFilter = Query(default="all"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    service: DependencyFleetService = Depends(dependency_fleet_service),
) -> DependencyRepositoryPage:
    return service.repositories(
        package_name,
        ecosystem=ecosystem,
        resolved_version=resolved_version,
        version_prefix=version_prefix,
        version_specifier=version_specifier,
        advisory_id=advisory_id,
        evidence=evidence,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{package_name:dependency_package}/advisory",
    response_model=DependencyAdvisoryOverview,
    operation_id="dependency_advisory_overview",
)
def dependency_advisory_overview(
    package_name: str,
    advisory_id: str = Query(min_length=1, max_length=200),
    affected_specifier: str = Query(min_length=1, max_length=4096),
    ecosystem: DependencyEcosystem = Query(default="pypi"),
    service: DependencyFleetService = Depends(dependency_fleet_service),
) -> DependencyAdvisoryOverview:
    return service.advisory(
        package_name,
        ecosystem=ecosystem,
        advisory_id=advisory_id,
        affected_specifier=affected_specifier,
    )


@router.get(
    "/{package_name:dependency_package}",
    response_model=DependencyPackageOverview,
    operation_id="dependency_overview",
)
def dependency_overview(
    package_name: str,
    ecosystem: DependencyEcosystem = Query(default="pypi"),
    service: DependencyFleetService = Depends(dependency_fleet_service),
) -> DependencyPackageOverview:
    return service.overview(package_name, ecosystem=ecosystem)
