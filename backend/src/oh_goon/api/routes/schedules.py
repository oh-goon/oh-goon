from fastapi import APIRouter, Depends, Query

from oh_goon.api.dependencies import schedule_service, shared_plan_schedule_service
from oh_goon.api.schemas.schedule import ScheduleRunRequest, SharedPlanScheduleCreateRequest
from oh_goon.core.models.schedule import (
    ScheduleCatalog,
    ScheduleDefinition,
    ScheduleDeleteRequest,
    ScheduleOccurrence,
    ScheduleOccurrenceRepositoryPage,
    ScheduleReplace,
)
from oh_goon.core.vocabulary import ScheduleOccurrenceFilter
from oh_goon.services.schedules import ScheduleService
from oh_goon.services.shared_plan_schedules import SharedPlanScheduleService

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("", response_model=ScheduleCatalog, operation_id="schedule_catalog")
def catalog(service: ScheduleService = Depends(schedule_service)) -> ScheduleCatalog:
    return service.catalog()


@router.post(
    "/{schedule_id}/from-shared-plan",
    response_model=ScheduleDefinition,
    status_code=201,
    operation_id="create_schedule_from_shared_plan",
)
def create_from_shared_plan(
    schedule_id: str,
    request: SharedPlanScheduleCreateRequest,
    service: SharedPlanScheduleService = Depends(shared_plan_schedule_service),
) -> ScheduleDefinition:
    return service.create(
        schedule_id,
        name=request.name,
        repository_ids=request.repository_ids,
        expected_repository_instance_ids=request.expected_repository_instance_ids,
        plan_key=request.plan_key,
        expected_plan_revision=request.expected_plan_revision,
        expected_plan_id=request.expected_plan_id,
        expected_execution_digest=request.expected_execution_digest,
        interval_minutes=request.interval_minutes,
        state=request.state,
        inputs=request.inputs,
    )


@router.put("/{schedule_id}", response_model=ScheduleDefinition, operation_id="replace_schedule")
def replace(
    schedule_id: str, request: ScheduleReplace, service: ScheduleService = Depends(schedule_service)
) -> ScheduleDefinition:
    return service.replace_preserving_authority(schedule_id, request)


@router.delete("/{schedule_id}", status_code=204, operation_id="delete_schedule")
def delete(
    schedule_id: str,
    request: ScheduleDeleteRequest,
    service: ScheduleService = Depends(schedule_service),
) -> None:
    service.delete(schedule_id, request)


@router.post(
    "/{schedule_id}/runs",
    response_model=ScheduleOccurrence,
    status_code=202,
    operation_id="start_schedule_run",
)
def start(
    schedule_id: str,
    request: ScheduleRunRequest,
    service: ScheduleService = Depends(schedule_service),
) -> ScheduleOccurrence:
    return service.start(
        schedule_id,
        request.schedule_run_id,
        expected_generation_id=request.expected_generation_id,
        expected_record_version=request.expected_record_version,
    )


@router.get(
    "/{schedule_id}/occurrences/{schedule_run_id}/repositories",
    response_model=ScheduleOccurrenceRepositoryPage,
    operation_id="schedule_occurrence_repositories",
)
def occurrence_repositories(
    schedule_id: str,
    schedule_run_id: str,
    status: ScheduleOccurrenceFilter = ScheduleOccurrenceFilter.ALL,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    service: ScheduleService = Depends(schedule_service),
) -> ScheduleOccurrenceRepositoryPage:
    return service.occurrence_repositories(
        schedule_id, schedule_run_id, status=status, offset=offset, limit=limit
    )
