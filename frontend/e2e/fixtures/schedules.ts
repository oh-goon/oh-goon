import { ScheduleExecutionStatus, ScheduleState } from "@generated/enums";
import type { FleetPlanTarget, ScheduleCatalog, ScheduleDefinition } from "@generated/models";

import { fixtureRepositoryInstanceId, PRIMARY_REPOSITORY } from "./identities";
import { FIXTURE_FLEET_PLAN_TARGET } from "./plans";
import { FIXTURE_FINISHED, FIXTURE_NOW } from "./time";

export function requireScheduleDefinition(
  schedules: readonly NonNullable<ScheduleCatalog["schedules"]>[number][],
  scheduleId: string,
): ScheduleDefinition {
  const definition = schedules.find((item) => item.definition.schedule_id === scheduleId)?.definition;
  if (definition === undefined) throw new Error(`Unknown fixture schedule ${scheduleId}`);
  return definition;
}

export function fixtureScheduleDefinition(
  scheduleId = "nightly-quality",
  name = "Nightly quality and security",
  repositoryIds: string[] = [PRIMARY_REPOSITORY.repository_id],
  intervalMinutes = 1440,
  state: ScheduleDefinition["state"] = ScheduleState.ACTIVE,
  target: FleetPlanTarget = FIXTURE_FLEET_PLAN_TARGET,
): ScheduleDefinition {
  return {
    schedule_id: scheduleId,
    generation_id: `generation:${scheduleId}`,
    record_version: 1,
    name,
    repository_ids: [...repositoryIds],
    repository_instance_ids: Object.fromEntries(repositoryIds.map((repositoryId) => [repositoryId, fixtureRepositoryInstanceId(repositoryId)])),
    target,
    interval_minutes: intervalMinutes,
    state,
    created_at: FIXTURE_NOW,
    updated_at: FIXTURE_NOW,
  };
}

export function fixtureScheduleStatus(): NonNullable<ScheduleCatalog["schedules"]>[number] {
  return {
    definition: fixtureScheduleDefinition(),
    latest_occurrence: {
      schedule_run_id: "schedule-run-fixture",
      schedule_id: "nightly-quality",
      status: ScheduleExecutionStatus.COMPLETED,
      started_at: FIXTURE_NOW,
      finished_at: FIXTURE_FINISHED,
      expected: 1,
      pending: 0,
      running: 0,
      successful: 1,
      attention_required: 0,
      failed: 0,
      cancelled: 0,
      not_started: 0,
      attention_count: 0,
    },
    next_run_at: "2026-08-22T16:00:00Z",
    missing_expected_occurrence_at: null,
  };
}
