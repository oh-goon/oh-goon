from fastapi import APIRouter, Depends, Query

from oh_goon.api.dependencies import scout_service
from oh_goon.scout.models import (
    ScoutDetailSection,
    ScoutFindingCategory,
    ScoutFindingsPage,
    ScoutGoonDetail,
    ScoutGoonLeadersPage,
    ScoutHealth,
    ScoutMetricDictionary,
    ScoutOverview,
    ScoutPlanDetail,
    ScoutRepositoryDetail,
    ScoutRunEvidence,
    ScoutSearchPage,
)
from oh_goon.scout.service import ScoutService

router = APIRouter(prefix="/scout", tags=["scout"])


@router.get("/overview", response_model=ScoutOverview, operation_id="scout_overview")
def scout_overview(
    hours: int = Query(default=24, ge=1, le=2160),
    limit: int = Query(default=20, ge=1, le=100),
    service: ScoutService = Depends(scout_service),
) -> ScoutOverview:
    return service.overview(hours=hours, limit=limit)


@router.get("/findings", response_model=ScoutFindingsPage, operation_id="scout_findings")
def scout_findings(
    hours: int = Query(default=24, ge=1, le=2160),
    category: ScoutFindingCategory | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    service: ScoutService = Depends(scout_service),
) -> ScoutFindingsPage:
    return service.findings(hours=hours, category=category, offset=offset, limit=limit)



@router.get(
    "/goon-leaders",
    response_model=ScoutGoonLeadersPage,
    operation_id="scout_goon_leaders",
)
def scout_goon_leaders(
    hours: int = Query(default=24, ge=1, le=2160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    service: ScoutService = Depends(scout_service),
) -> ScoutGoonLeadersPage:
    return service.goon_leaders(hours=hours, offset=offset, limit=limit)


@router.get(
    "/repositories/{repository_id}",
    response_model=ScoutRepositoryDetail,
    operation_id="scout_repository",
)
def scout_repository(
    repository_id: str,
    hours: int = Query(default=24, ge=1, le=2160),
    plan_id: str | None = Query(default=None, min_length=1),
    section: ScoutDetailSection | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    service: ScoutService = Depends(scout_service),
) -> ScoutRepositoryDetail:
    return service.repository(
        repository_id, hours=hours, plan_id=plan_id, section=section, offset=offset, limit=limit
    )


@router.get(
    "/plans/{plan_id}",
    response_model=ScoutPlanDetail,
    operation_id="scout_plan",
)
def scout_plan(
    plan_id: str,
    hours: int = Query(default=24, ge=1, le=2160),
    section: ScoutDetailSection | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    service: ScoutService = Depends(scout_service),
) -> ScoutPlanDetail:
    return service.plan(plan_id, hours=hours, section=section, offset=offset, limit=limit)


@router.get("/goons", response_model=ScoutGoonDetail, operation_id="scout_goon")
def scout_goon(
    name: str = Query(min_length=1),
    source_sha256: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    hours: int = Query(default=24, ge=1, le=2160),
    section: ScoutDetailSection | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    service: ScoutService = Depends(scout_service),
) -> ScoutGoonDetail:
    return service.goon(
        name, source_sha256=source_sha256, hours=hours, section=section, offset=offset, limit=limit
    )


@router.get("/search", response_model=ScoutSearchPage, operation_id="scout_search")
def scout_search(
    q: str = Query(min_length=1, max_length=120),
    hours: int = Query(default=24, ge=1, le=2160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    service: ScoutService = Depends(scout_service),
) -> ScoutSearchPage:
    return service.search(query=q, hours=hours, offset=offset, limit=limit)


@router.get(
    "/runs/{run_id}",
    response_model=ScoutRunEvidence,
    operation_id="scout_run",
)
def scout_run(
    run_id: str,
    service: ScoutService = Depends(scout_service),
) -> ScoutRunEvidence:
    return service.run(run_id)


@router.get("/health", response_model=ScoutHealth, operation_id="scout_health")
def scout_health(
    service: ScoutService = Depends(scout_service),
) -> ScoutHealth:
    return service.health()


@router.get(
    "/metrics",
    response_model=ScoutMetricDictionary,
    operation_id="scout_metric_dictionary",
)
def scout_metric_dictionary(
    service: ScoutService = Depends(scout_service),
) -> ScoutMetricDictionary:
    return service.metric_dictionary()
