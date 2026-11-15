import { ScheduleExecutionStatus, ScheduleState } from "@generated/enums";
import type { FleetPlanTarget, ScheduleCatalog, ScheduleDefinition, ScheduleReplace, SharedPlanDocument, SharedPlanScheduleCreateRequest } from "@generated/models";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

import { fixtureRepositoryInstanceId, hasExactRepositoryMembership, PRIMARY_REPOSITORY } from "./identities";
import { FIXTURE_FLEET_PLAN_TARGET } from "./plans";
import { FIXTURE_FINISHED, FIXTURE_NOW } from "./time";

type FixtureScheduleDefinitionResult = ScheduleDefinition | Exclude<WriteCommandResult<never>, { outcome: "saved" }>;

export function reviewedFixtureScheduleDefinition(
  scheduleId: string,
  request: SharedPlanScheduleCreateRequest,
  document: SharedPlanDocument,
  target: FleetPlanTarget,
  catalog: ScheduleCatalog,
): FixtureScheduleDefinitionResult {
  if (!hasExactRepositoryMembership(request.repository_ids, request.expected_repository_instance_ids)) {
    return { outcome: "write-error", diagnosticId: `fixture:schedule-targets:${scheduleId}`, recovery: "blocked" };
  }
  if (request.repository_ids.some((id) => request.expected_repository_instance_ids[id] !== fixtureRepositoryInstanceId(id))) {
    return { outcome: "stale", diagnosticId: `fixture:schedule-repository-instance:${scheduleId}` };
  }
  if (request.expected_plan_revision !== document.sha256) {
    return { outcome: "stale", diagnosticId: `fixture:schedule-plan-revision:${scheduleId}` };
  }
  if (request.expected_plan_id !== target.plan.plan_id || request.expected_execution_digest !== target.identity.digest) {
    return { outcome: "write-error", diagnosticId: `fixture:schedule-execution-identity:${scheduleId}`, recovery: "blocked" };
  }
  const existing = catalog.schedules?.find((item) => item.definition.schedule_id === scheduleId)?.definition;
  const definition = fixtureScheduleDefinition(scheduleId, request.name, request.repository_ids, request.interval_minutes, request.state, target, {
    generation_id: existing?.generation_id ?? `generation:${crypto.randomUUID()}`,
    record_version: existing?.record_version ?? 0,
  });
  if (existing && scheduleCreationIdentity(existing) !== scheduleCreationIdentity(definition)) {
    return { outcome: "stale", diagnosticId: `fixture:schedule-create-conflict:${scheduleId}` };
  }
  return existing ?? definition;
}

function scheduleCreationIdentity(definition: ScheduleDefinition): string {
  return canonicalIdentity({
    name: definition.name,
    repositoryIds: definition.repository_ids,
    repositoryInstanceIds: definition.repository_instance_ids,
    target: definition.target,
    intervalMinutes: definition.interval_minutes,
    state: definition.state,
  });
}

export function createdFixtureScheduleCatalog(catalog: ScheduleCatalog, definition: ScheduleDefinition): ScheduleCatalog {
  const schedules = catalog.schedules ?? [];
  if (schedules.some((item) => item.definition.schedule_id === definition.schedule_id)) return catalog;
  return { ...catalog, schedules: [{ definition, latest_occurrence: null, next_run_at: "2026-08-22T16:00:00Z", missing_expected_occurrence_at: null }, ...schedules] };
}

export function replaceFixtureScheduleMetadata(current: ScheduleDefinition, request: ScheduleReplace): FixtureScheduleDefinitionResult {
  if (current.generation_id !== request.generation_id || current.record_version !== request.record_version) {
    return { outcome: "stale", diagnosticId: `fixture:schedule-authority:${current.schedule_id}` };
  }
  if (canonicalIdentity({ repositories: request.repository_ids, instances: request.repository_instance_ids, target: request.target })
    !== canonicalIdentity({ repositories: current.repository_ids, instances: current.repository_instance_ids, target: current.target })) {
    return { outcome: "write-error", diagnosticId: `fixture:schedule-execution-authority:${current.schedule_id}`, recovery: "blocked" };
  }
  return { ...current, ...request, schedule_id: current.schedule_id, updated_at: FIXTURE_FINISHED, record_version: current.record_version + 1 };
}

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
  authority: Pick<ScheduleDefinition, "generation_id" | "record_version"> = { generation_id: `generation:${scheduleId}`, record_version: 1 },
): ScheduleDefinition {
  return {
    schedule_id: scheduleId,
    generation_id: authority.generation_id,
    record_version: authority.record_version,
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
