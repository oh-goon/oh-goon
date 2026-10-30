from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from oh_goon.api.dependencies import fleet_run_control_plane
from oh_goon.api.schemas.fleet import AgentCampaignResult, FleetRunChildRequest, FleetRunRequest
from oh_goon.core.models.execution_observability import ExecutionTimeBucket, FleetExecutionTimeline
from oh_goon.core.models.fleet_failures import (
    FleetFailureGroupPage,
    FleetFailureInventory,
    FleetFailureRepositoryPage,
)
from oh_goon.core.models.game_tape import (
    MAX_ARTIFACT_OBSERVATION_BYTES,
    MAX_ARTIFACT_OBSERVATION_COUNT,
    MAX_ARTIFACT_OBSERVATION_SECONDS,
    GameTapeRunDiff,
)
from oh_goon.core.models.game_tape_failure_history import GameTapeFailureHistory
from oh_goon.core.models.game_tape_live_output import GameTapeLiveOutputBatch
from oh_goon.core.models.game_tape_performance import GameTapePerformanceReport
from oh_goon.core.models.run import (
    FleetRunDetail,
    FleetRunHistoryPage,
    FleetRunOverview,
    FleetRunState,
)
from oh_goon.core.models.run_events import FleetRunEventBatch
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunSource, FleetRunStatus
from oh_goon.services.enforcer_run_receipt import enforcer_run_receipt, render_enforcer_yaml
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane

router = APIRouter(prefix="/fleet/runs", tags=["fleet"])


@router.get("", response_model=FleetRunHistoryPage, operation_id="fleet_run_history")
def fleet_run_history(
    limit: int = Query(default=20, ge=1, le=100),
    before_revision: int | None = Query(default=None, ge=1),
    outcome: FleetRunOutcome | None = Query(default=None),
    status: FleetRunStatus | None = Query(default=None),
    source: FleetRunSource | None = Query(default=None),
    repository_id: str | None = Query(default=None, min_length=1),
    from_at: datetime | None = Query(default=None),
    to_at: datetime | None = Query(default=None),
    plan_query: str | None = Query(default=None, max_length=240),
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetRunHistoryPage:
    return control_plane.history(
        limit=limit,
        before_revision=before_revision,
        outcome=outcome,
        status=status,
        source=source,
        repository_id=repository_id,
        from_at=from_at,
        to_at=to_at,
        plan_query=plan_query,
    )


@router.get("/overview", response_model=FleetRunOverview, operation_id="fleet_run_overview")
def fleet_run_overview(
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetRunOverview:
    return control_plane.overview()


@router.get(
    "/campaigns/{campaign_id}",
    response_model=AgentCampaignResult,
    operation_id="get_agent_campaign",
)
def get_agent_campaign(
    campaign_id: str,
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> AgentCampaignResult:
    return AgentCampaignResult.model_validate(control_plane.agent_campaign(campaign_id))


@router.get(
    "/observations",
    response_model=FleetExecutionTimeline,
    operation_id="fleet_run_execution_observations",
)
def fleet_run_execution_observations(
    from_at: datetime = Query(),
    to_at: datetime = Query(),
    bucket: ExecutionTimeBucket = Query(default="day"),
    plan_query: str | None = Query(default=None, max_length=240),
    repository_query: str | None = Query(default=None, max_length=240),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=100),
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetExecutionTimeline:
    return control_plane.execution_timeline(
        from_at=from_at,
        to_at=to_at,
        bucket=bucket,
        plan_query=plan_query,
        repository_query=repository_query,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/failures",
    response_model=FleetFailureInventory,
    operation_id="fleet_failure_inventory",
)
def fleet_failure_inventory(
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetFailureInventory:
    return control_plane.failure_inventory()


@router.get(
    "/failures/groups",
    response_model=FleetFailureGroupPage,
    operation_id="fleet_failure_groups",
)
def fleet_failure_groups(
    query: str | None = Query(default=None, max_length=160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetFailureGroupPage:
    return control_plane.failure_groups(query=query, offset=offset, limit=limit)


@router.get(
    "/failures/groups/{group_id}/repositories",
    response_model=FleetFailureRepositoryPage,
    operation_id="fleet_failure_repositories",
)
def fleet_failure_repositories(
    group_id: str,
    query: str | None = Query(default=None, max_length=240),
    signature_id: str | None = Query(default=None, max_length=160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetFailureRepositoryPage:
    return control_plane.failure_repositories(
        group_id,
        query=query,
        signature_id=signature_id,
        offset=offset,
        limit=limit,
    )


@router.post("", response_model=FleetRunState, status_code=202, operation_id="start_fleet_run")
def start_fleet_run(
    request: FleetRunRequest,
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetRunState:
    return control_plane.start(
        request.action,
        target=request.target,
        repository_ids=set(request.repository_ids) if request.repository_ids is not None else None,
        all_repositories=request.all_repositories,
        expected_repository_instance_ids=request.repository_instance_ids,
        fleet_run_id=request.fleet_run_id,
        requested_priority=request.requested_priority,
    )


@router.post(
    "/{fleet_run_id}/readmit",
    response_model=FleetRunState,
    status_code=202,
    operation_id="readmit_fleet_run",
)
def readmit_fleet_run(
    fleet_run_id: str,
    request: FleetRunChildRequest,
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetRunState:
    return control_plane.readmit(fleet_run_id, new_fleet_run_id=request.new_fleet_run_id)


@router.post(
    "/{fleet_run_id}/rerun",
    response_model=FleetRunState,
    status_code=202,
    operation_id="rerun_fleet_run",
)
def rerun_fleet_run(
    fleet_run_id: str,
    request: FleetRunChildRequest,
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetRunState:
    return control_plane.rerun(fleet_run_id, new_fleet_run_id=request.new_fleet_run_id)


@router.post(
    "/{fleet_run_id}/cancel",
    response_model=FleetRunState,
    status_code=202,
    operation_id="cancel_fleet_run",
)
def cancel_fleet_run(
    fleet_run_id: str,
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetRunState:
    return control_plane.cancel(fleet_run_id)


@router.get(
    "/{fleet_run_id}/enforcer.yml",
    response_class=Response,
    operation_id="get_fleet_run_enforcer_receipt",
)
def get_fleet_run_enforcer_receipt(
    fleet_run_id: str,
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> Response:
    receipt = enforcer_run_receipt(control_plane.get(fleet_run_id))
    return Response(
        content=render_enforcer_yaml(receipt),
        media_type="application/yaml",
        headers={
            "Content-Disposition": f'attachment; filename="{fleet_run_id}-enforcer.yml"',
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/{fleet_run_id}/compare/{baseline_run_id}",
    response_model=GameTapeRunDiff,
    operation_id="compare_fleet_runs",
)
def compare_fleet_runs(
    fleet_run_id: str,
    baseline_run_id: str,
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> GameTapeRunDiff:
    return control_plane.game_tape_comparison(fleet_run_id, baseline_run_id)


@router.get(
    "/{fleet_run_id}/performance",
    response_model=GameTapePerformanceReport,
    operation_id="game_tape_performance",
)
def game_tape_performance(
    fleet_run_id: str,
    sample_limit: int = Query(default=10, ge=1, le=20),
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> GameTapePerformanceReport:
    return control_plane.game_tape_performance(fleet_run_id, sample_limit=sample_limit)


@router.get(
    "/{fleet_run_id}/failure-history",
    response_model=GameTapeFailureHistory,
    operation_id="game_tape_failure_history",
)
def game_tape_failure_history(
    fleet_run_id: str,
    repository_id: str = Query(min_length=1),
    step_id: str = Query(min_length=1, max_length=80),
    sample_limit: int = Query(default=10, ge=1, le=20),
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> GameTapeFailureHistory:
    return control_plane.game_tape_failure_history(
        fleet_run_id,
        repository_id=repository_id,
        step_id=step_id,
        sample_limit=sample_limit,
    )


@router.get("/{fleet_run_id}", response_model=FleetRunDetail, operation_id="get_fleet_run")
def get_fleet_run(
    fleet_run_id: str,
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
    observe_artifacts: bool = False,
    artifact_limit: Annotated[int | None, Query(ge=0, le=MAX_ARTIFACT_OBSERVATION_COUNT)] = None,
    artifact_byte_budget: Annotated[
        int | None, Query(ge=0, le=MAX_ARTIFACT_OBSERVATION_BYTES)
    ] = None,
    artifact_time_budget_seconds: Annotated[
        float | None, Query(gt=0, le=MAX_ARTIFACT_OBSERVATION_SECONDS)
    ] = None,
) -> FleetRunDetail:
    if observe_artifacts or any(
        value is not None
        for value in (artifact_limit, artifact_byte_budget, artifact_time_budget_seconds)
    ):
        return control_plane.get(
            fleet_run_id,
            observe_artifacts=observe_artifacts,
            artifact_limit=artifact_limit,
            artifact_byte_budget=artifact_byte_budget,
            artifact_time_budget_seconds=artifact_time_budget_seconds,
        )
    return control_plane.get(fleet_run_id)


@router.get(
    "/{fleet_run_id}/live-output",
    response_model=GameTapeLiveOutputBatch,
    operation_id="game_tape_live_output",
)
def game_tape_live_output(
    fleet_run_id: str,
    after: int = Query(default=0, ge=0),
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> GameTapeLiveOutputBatch:
    return control_plane.game_tape_live_output(fleet_run_id, after=after)


@router.get(
    "/{fleet_run_id}/events",
    response_model=FleetRunEventBatch,
    operation_id="fleet_run_events",
)
def fleet_run_events(
    fleet_run_id: str,
    after: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=100),
    control_plane: FleetRunControlPlane = Depends(fleet_run_control_plane),
) -> FleetRunEventBatch:
    return control_plane.events(fleet_run_id, after, limit=limit)
