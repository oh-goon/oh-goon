from fastapi import APIRouter, Depends

from oh_goon.api.dependencies import (
    goon_service,
    league_lineup_service,
    league_service,
    shared_plan_service,
)
from oh_goon.api.schemas.league import (
    LeagueLineupSaveRequest,
    SharedPlanFromTemplateRequest,
    SharedPlanInputInspection,
    SharedPlanLockPreviewRequest,
    SharedPlanPreviewRequest,
    SharedPlanRunRequest,
    SharedPlanSaveRequest,
    TeamRunRequest,
)
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import FleetPlanInvalid, StateConflict
from oh_goon.core.models.league import LeagueMap
from oh_goon.core.models.plan_template import PlanTemplateCatalog
from oh_goon.core.models.run import FleetRunState
from oh_goon.core.models.shared_plan import (
    LeagueLineupCatalog,
    LeagueLineupDocument,
    LeagueLineupInspection,
    SharedPlanCatalog,
    SharedPlanDocument,
    SharedPlanLockPreview,
    SharedPlanPreview,
    SharedPlanRunHistory,
)
from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic
from oh_goon.services.goon import GoonService
from oh_goon.services.league import LeagueService
from oh_goon.services.league_lineups import LeagueLineupService
from oh_goon.services.shared_plans import SharedPlanService

router = APIRouter(prefix="/league", tags=["league"])


@router.get("", response_model=LeagueMap, operation_id="league_map")
def league_map(service: LeagueService = Depends(league_service)) -> LeagueMap:
    """Scan configured local folders and return every Git team plus goon.yml state."""
    return service.map()


@router.post("/team-runs", response_model=FleetRunState, status_code=202, operation_id="run_team")
def run_team(
    request: TeamRunRequest,
    service: GoonService = Depends(goon_service),
) -> FleetRunState:
    """Run exactly one discovered Team through its repository-root goon.yml."""
    try:
        return service.run(
            request.repository,
            expected_pipeline_sha256=request.expected_pipeline_sha256,
            expected_repository_instance_id=request.expected_repository_instance_id,
            inputs=request.inputs,
            fleet_run_id=request.fleet_run_id,
        )
    except TrustProtocolError as exc:
        # The Fleet lifecycle owns trust. HTTP exposes only a stable conflict.
        raise StateConflict(
            "Fleet Plan execution requires valid authenticated admission authority"
        ) from exc


@router.get(
    "/plan-templates",
    response_model=PlanTemplateCatalog,
    operation_id="plan_templates",
)
def plan_templates(
    service: SharedPlanService = Depends(shared_plan_service),
) -> PlanTemplateCatalog:
    """List shipped Plan templates; creating a saved Shared Plan is always explicit."""
    return service.templates()


@router.post(
    "/shared-plans/from-template",
    response_model=SharedPlanDocument,
    operation_id="create_shared_plan_from_template",
)
def create_shared_plan_from_template(
    request: SharedPlanFromTemplateRequest,
    service: SharedPlanService = Depends(shared_plan_service),
) -> SharedPlanDocument:
    return service.create_from_template(request.template_key, request.expected_template_sha256)


@router.get("/shared-plans", response_model=SharedPlanCatalog, operation_id="shared_plans")
def shared_plans(
    service: SharedPlanService = Depends(shared_plan_service),
) -> SharedPlanCatalog:
    """List plan.yml files stored by oh-goon outside repository roots."""
    return service.catalog()


@router.get(
    "/shared-plans/{plan_key}",
    response_model=SharedPlanDocument,
    operation_id="shared_plan",
)
def shared_plan(
    plan_key: str,
    service: SharedPlanService = Depends(shared_plan_service),
) -> SharedPlanDocument:
    return service.read(plan_key, include_inputs=True)


@router.get(
    "/shared-plans/{plan_key}/runs",
    response_model=SharedPlanRunHistory,
    operation_id="shared_plan_runs",
)
def shared_plan_runs(
    plan_key: str,
    limit: int = 20,
    before_revision: int | None = None,
    service: SharedPlanService = Depends(shared_plan_service),
) -> SharedPlanRunHistory:
    return service.history(plan_key, limit=limit, before_revision=before_revision)


@router.put(
    "/shared-plans/{plan_key}",
    response_model=SharedPlanDocument,
    operation_id="save_shared_plan",
)
def save_shared_plan(
    plan_key: str,
    request: SharedPlanSaveRequest,
    service: SharedPlanService = Depends(shared_plan_service),
) -> SharedPlanDocument:
    return service.save(plan_key, request.content, expected_revision=request.expected_revision)


@router.post(
    "/shared-plan-lock-previews",
    response_model=SharedPlanLockPreview,
    operation_id="preview_shared_plan_lock",
)
def preview_shared_plan_lock(
    request: SharedPlanLockPreviewRequest,
    service: SharedPlanService = Depends(shared_plan_service),
) -> SharedPlanLockPreview:
    """Render the exact backend-resolved goon.lock for one Shared Plan input set."""
    return service.lock_preview(
        request.plan_key, inputs=request.inputs, expected_revision=request.expected_revision
    )


@router.post(
    "/shared-plan-input-inspections",
    response_model=SharedPlanInputInspection,
    response_model_by_alias=True,
    operation_id="inspect_shared_plan_inputs",
)
def inspect_shared_plan_inputs(
    request: SharedPlanLockPreviewRequest,
    service: SharedPlanService = Depends(shared_plan_service),
) -> SharedPlanInputInspection:
    """Inspect only the exact pinned Plan+input cut; rejection is not admission."""
    try:
        plan_key, sha256, plan_id = service.inspect_inputs(
            request.plan_key,
            expected_revision=request.expected_revision,
            inputs=request.inputs,
        )
    except FleetPlanInvalid as exc:
        return SharedPlanInputInspection(status="rejected", diagnostic=safe_plan_diagnostic(exc))
    return SharedPlanInputInspection(
        status="accepted", plan_key=plan_key, sha256=sha256, plan_id=plan_id
    )


@router.post(
    "/shared-plan-previews",
    response_model=SharedPlanPreview,
    operation_id="preview_shared_plan",
)
def preview_shared_plan(
    request: SharedPlanPreviewRequest,
    service: SharedPlanService = Depends(shared_plan_service),
) -> SharedPlanPreview:
    """Read-only Enforcer preview of one shared Plan across explicit current Teams."""
    return service.preview(
        request.plan_key,
        repository_ids=set(request.repository_ids),
        inputs=request.inputs,
        expected_revision=request.expected_revision,
    )


@router.post(
    "/shared-plan-runs",
    response_model=FleetRunState,
    status_code=202,
    operation_id="run_shared_plan",
)
def run_shared_plan(
    request: SharedPlanRunRequest,
    service: SharedPlanService = Depends(shared_plan_service),
) -> FleetRunState:
    """Run one shared Plan on explicit Teams; repository goon.yml files are ignored."""
    try:
        return service.run(
            request.plan_key,
            repository_ids=set(request.repository_ids),
            inputs=request.inputs,
            fleet_run_id=request.fleet_run_id,
            expected_plan_id=request.expected_plan_id,
            expected_execution_digest=request.expected_execution_digest,
            expected_plan_revision=request.expected_plan_revision,
            repository_instance_ids=request.repository_instance_ids,
        )
    except TrustProtocolError as exc:
        # Trust is enforced by the original lifecycle, not by HTTP. Present a
        # stable conflict without leaking signer/root/enrollment internals.
        raise StateConflict(
            "Fleet Plan execution requires valid authenticated admission authority"
        ) from exc


@router.get("/lineups", response_model=LeagueLineupCatalog, operation_id="league_lineups")
def lineups(
    service: LeagueLineupService = Depends(league_lineup_service),
) -> LeagueLineupCatalog:
    return service.catalog()


@router.get(
    "/lineups/{lineup_key}",
    response_model=LeagueLineupDocument,
    operation_id="league_lineup",
)
def lineup(
    lineup_key: str,
    service: LeagueLineupService = Depends(league_lineup_service),
) -> LeagueLineupDocument:
    return service.read(lineup_key)


@router.get(
    "/lineups/{lineup_key}/inspection",
    response_model=LeagueLineupInspection,
    operation_id="inspect_league_lineup",
)
def inspect_lineup(
    lineup_key: str,
    service: LeagueLineupService = Depends(league_lineup_service),
) -> LeagueLineupInspection:
    return service.inspect(lineup_key)


@router.put(
    "/lineups/{lineup_key}",
    response_model=LeagueLineupDocument,
    operation_id="save_lineup",
)
def save_lineup(
    lineup_key: str,
    request: LeagueLineupSaveRequest,
    service: LeagueLineupService = Depends(league_lineup_service),
) -> LeagueLineupDocument:
    return service.save(
        lineup_key,
        name=request.name,
        repositories=request.repositories,
        expected_revision=request.expected_revision,
    )
