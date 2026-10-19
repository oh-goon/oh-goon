import { readOperation } from "@/app/api/httpTransport";
import { writeOperation } from "@/app/api/mutationTransport";
import type { ApiClient } from "@/domain/api/ApiClient";
import {
  operations as op,
  type OperationCall,
  type OperationName,
} from "@generated/operations";
import type { MutationController, MutationScope } from "@/domain/concurrency/MutationController";


export function createApiClient(mutations: MutationController): ApiClient {
  const write = <Name extends OperationName>(
    scope: MutationScope,
    name: Name,
    call: OperationCall<Name>,
  ) => writeOperation(mutations, scope, name, call);

  return {
    health: (options) => readOperation(op.health.operationId, {}, options),
    privacy: (options) => readOperation(op.privacy_status.operationId, {}, options),
    fleet: (options) => readOperation(op.fleet_snapshot.operationId, {}, options),
    scoutOverview: (hours = 24, limit = 20, options) => readOperation(
      op.scout_overview.operationId,
      { query: { hours, limit } },
      options,
    ),
    scoutFindings: (hours = 24, category, offset = 0, limit = 20, options) => readOperation(
      op.scout_findings.operationId,
      { query: { hours, offset, limit, ...(category === undefined ? {} : { category }) } },
      options,
    ),
    scoutRepository: (repositoryId, hours = 24, planId, section, offset = 0, limit = 100, options) => readOperation(
      op.scout_repository.operationId,
      { path: { repository_id: repositoryId }, query: { hours, offset, limit, ...(planId === undefined ? {} : { plan_id: planId }), ...(section === undefined ? {} : { section }) } },
      options,
    ),
    scoutPlan: (planId, hours = 24, section, offset = 0, limit = 100, options) => readOperation(
      op.scout_plan.operationId,
      { path: { plan_id: planId }, query: { hours, offset, limit, ...(section === undefined ? {} : { section }) } },
      options,
    ),
    scoutGoon: (name, sourceSha256, hours = 24, section, offset = 0, limit = 100, options) => readOperation(
      op.scout_goon.operationId,
      { query: { name, hours, offset, limit, ...(sourceSha256 === undefined ? {} : { source_sha256: sourceSha256 }), ...(section === undefined ? {} : { section }) } },
      options,
    ),
    scoutGoonLeaders: (hours = 24, offset = 0, limit = 20, options) => readOperation(
      op.scout_goon_leaders.operationId,
      { query: { hours, offset, limit } },
      options,
    ),
    scoutSearch: (query, hours = 24, offset = 0, limit = 20, options) => readOperation(
      op.scout_search.operationId,
      { query: { q: query, hours, offset, limit } },
      options,
    ),
    scoutMetrics: (options) => readOperation(op.scout_metric_dictionary.operationId, {}, options),
    scoutHealth: (options) => readOperation(op.scout_health.operationId, {}, options),
    scoutRun: (runId, options) => readOperation(
      op.scout_run.operationId,
      { path: { run_id: runId } },
      options,
    ),
    fleetRunOverview: (options) => readOperation(op.fleet_run_overview.operationId, {}, options),
    fleetRunExecutionObservations: (query, options) => readOperation(op.fleet_run_execution_observations.operationId, { query }, options),
    fleetFailureInventory: (options) => readOperation(op.fleet_failure_inventory.operationId, {}, options),
    fleetFailureGroups: (query, offset = 0, limit = 50, options) => readOperation(
      op.fleet_failure_groups.operationId,
      { query: { ...(query ? { query } : {}), offset, limit } },
      options,
    ),
    fleetFailureRepositories: (groupId, query, signatureId, offset = 0, limit = 50, options) => readOperation(
      op.fleet_failure_repositories.operationId,
      { path: { group_id: groupId }, query: { ...(query ? { query } : {}), ...(signatureId ? { signature_id: signatureId } : {}), offset, limit } },
      options,
    ),
    fleetRunHistory: (beforeRevision, limit = 12, outcome, status, source, repositoryId, fromAt, toAt, planQuery, options) => readOperation(
      op.fleet_run_history.operationId,
      { query: { limit, ...(beforeRevision === undefined ? {} : { before_revision: beforeRevision }), ...(outcome === undefined ? {} : { outcome }), ...(status === undefined ? {} : { status }), ...(source === undefined ? {} : { source }), ...(repositoryId === undefined ? {} : { repository_id: repositoryId }), ...(fromAt === undefined ? {} : { from_at: fromAt }), ...(toAt === undefined ? {} : { to_at: toAt }), ...(planQuery === undefined || planQuery === "" ? {} : { plan_query: planQuery }) } },
      options,
    ),
    fleetRunDetail: (runId, options) => readOperation(
      op.get_fleet_run.operationId,
      { path: { fleet_run_id: runId } },
      options,
    ),
    compareFleetRuns: (runId, baselineRunId, options) => readOperation(
      op.compare_fleet_runs.operationId,
      { path: { fleet_run_id: runId, baseline_run_id: baselineRunId } },
      options,
    ),
    gameTapePerformance: (runId, sampleLimit = 10, options) => readOperation(
      op.game_tape_performance.operationId,
      { path: { fleet_run_id: runId }, query: { sample_limit: sampleLimit } },
      options,
    ),
    gameTapeFailureHistory: (runId, repositoryId, stepId, sampleLimit = 10, options) => readOperation(
      op.game_tape_failure_history.operationId,
      { path: { fleet_run_id: runId }, query: { repository_id: repositoryId, step_id: stepId, sample_limit: sampleLimit } },
      options,
    ),
    gameTapeLiveOutput: (runId, after = 0, options) => readOperation(
      op.game_tape_live_output.operationId,
      { path: { fleet_run_id: runId }, query: { after } },
      options,
    ),
    dependencySearch: (query, offset = 0, limit = 20, options) => readOperation(
      op.dependency_search.operationId,
      { query: { q: query, offset, limit } },
      options,
    ),
    dependencyFleetCoverage: (options) => readOperation(
      op.dependency_fleet_coverage.operationId,
      {},
      options,
    ),
    dependencyOverview: (packageName, options) => readOperation(
      op.dependency_overview.operationId,
      { path: { package_name: packageName } },
      options,
    ),
    dependencyAdvisory: (packageName, advisoryId, affectedSpecifier, options) => readOperation(
      op.dependency_advisory_overview.operationId,
      { path: { package_name: packageName }, query: { advisory_id: advisoryId, affected_specifier: affectedSpecifier } },
      options,
    ),
    dependencyCoverageRepositories: (packageName, coverage, offset = 0, limit = 20, options) => readOperation(
      op.dependency_coverage_repositories.operationId,
      { path: { package_name: packageName }, query: { coverage, offset, limit } },
      options,
    ),
    dependencyRepositories: (packageName, resolvedVersion, versionPrefix, evidence = "all", offset = 0, limit = 20, versionSpecifier, advisoryId, options) => readOperation(
      op.dependency_repositories.operationId,
      { path: { package_name: packageName }, query: { evidence, offset, limit, ...(resolvedVersion === undefined ? {} : { resolved_version: resolvedVersion }), ...(versionPrefix === undefined ? {} : { version_prefix: versionPrefix }), ...(versionSpecifier === undefined ? {} : { version_specifier: versionSpecifier }), ...(advisoryId === undefined ? {} : { advisory_id: advisoryId }) } },
      options,
    ),
    repositoryDependencies: (repositoryId, offset = 0, limit = 20, options) => readOperation(
      op.repository_dependencies.operationId,
      { path: { repository_id: repositoryId }, query: { offset, limit } },
      options,
    ),
    materializeDependencyTargets: (request) => write(
      "dependency-selections",
      op.materialize_dependency_targets.operationId,
      { body: request },
    ),
    dependencyTargetSelection: (queryId, options) => readOperation(
      op.dependency_target_selection.operationId,
      { path: { query_id: queryId } },
      options,
    ),
    schedules: (options) => readOperation(op.schedule_catalog.operationId, {}, options),
    createScheduleFromSharedPlan: (scheduleId, schedule) => write(
      "schedules",
      op.create_schedule_from_shared_plan.operationId,
      { path: { schedule_id: scheduleId }, body: schedule },
    ),
    updateSchedule: (scheduleId, schedule) => write(
      "schedules",
      op.replace_schedule.operationId,
      { path: { schedule_id: scheduleId }, body: schedule },
    ),
    deleteSchedule: (scheduleId, request) => write(
      "schedules",
      op.delete_schedule.operationId,
      { path: { schedule_id: scheduleId }, body: request },
    ),
    startSchedule: (scheduleId, scheduleRunId, expectedGenerationId, expectedRecordVersion) => write(
      "fleet-run",
      op.start_schedule_run.operationId,
      {
        path: { schedule_id: scheduleId },
        body: {
          schedule_run_id: scheduleRunId,
          expected_generation_id: expectedGenerationId,
          expected_record_version: expectedRecordVersion,
        },
      },
    ),
    scheduleOccurrenceRepositories: (scheduleId, scheduleRunId, status, offset = 0, limit = 50, options) => readOperation(
      op.schedule_occurrence_repositories.operationId,
      {
        path: { schedule_id: scheduleId, schedule_run_id: scheduleRunId },
        query: { ...(status === undefined ? {} : { status }), offset, limit },
      },
      options,
    ),
    fleetCommands: (options) => readOperation(op.fleet_command_center.operationId, {}, options),
    executorStatus: (options) => readOperation(op.fleet_executor_status.operationId, {}, options),
    placementQueue: (options) => readOperation(op.fleet_placement_queue.operationId, {}, options),
    drainExecutor: (executorId, expectedGeneration, expectedOperationalRevision) => write(
      "fleet-run",
      op.drain_executor.operationId,
      {
        path: { executor_id: executorId },
        body: { expected_generation: expectedGeneration, expected_operational_revision: expectedOperationalRevision },
      },
    ),
    activateExecutor: (executorId, expectedGeneration, expectedOperationalRevision) => write(
      "fleet-run",
      op.activate_executor.operationId,
      {
        path: { executor_id: executorId },
        body: { expected_generation: expectedGeneration, expected_operational_revision: expectedOperationalRevision },
      },
    ),
    revokeExecutorTrust: (executorId, expectedGeneration, expectedOperationalRevision) => write(
      "fleet-run",
      op.revoke_executor_trust.operationId,
      {
        path: { executor_id: executorId },
        body: { expected_generation: expectedGeneration, expected_operational_revision: expectedOperationalRevision },
      },
    ),
    restoreExecutorTrust: (executorId, expectedGeneration, expectedOperationalRevision) => write(
      "fleet-run",
      op.restore_executor_trust.operationId,
      {
        path: { executor_id: executorId },
        body: { expected_generation: expectedGeneration, expected_operational_revision: expectedOperationalRevision },
      },
    ),
    cancelQueuedAdmission: (runId, expectedSequence) => write(
      "fleet-run",
      op.cancel_queued_admission.operationId,
      { path: { fleet_run_id: runId }, body: { expected_sequence: expectedSequence } },
    ),
    fleetSecurity: (options) => readOperation(op.fleet_security.operationId, {}, options),
    adoptUvPreview: (options) => readOperation(op.adopt_uv_preview.operationId, {}, options),
    startFleetRun: (fleetRunId, action, options) => write(
      "fleet-run",
      op.start_fleet_run.operationId,
      {
        body: {
          fleet_run_id: fleetRunId,
          action,
          target: options?.target ?? null,
          repository_ids: options?.repositoryIds ?? null,
          repository_instance_ids: options?.repositoryInstanceIds ?? null,
        },
      },
    ),
    fleetRunEvents: (runId, after, options) => readOperation(
      op.fleet_run_events.operationId,
      { path: { fleet_run_id: runId }, query: { after } },
      options,
    ),
    cancelFleetRun: (runId) => write(
      "fleet-run",
      op.cancel_fleet_run.operationId,
      { path: { fleet_run_id: runId } },
    ),
    readmitFleetRun: (runId, newFleetRunId) => write(
      "fleet-run",
      op.readmit_fleet_run.operationId,
      { path: { fleet_run_id: runId }, body: { new_fleet_run_id: newFleetRunId } },
    ),
    rerunFleetRun: (runId, newFleetRunId) => write(
      "fleet-run",
      op.rerun_fleet_run.operationId,
      { path: { fleet_run_id: runId }, body: { new_fleet_run_id: newFleetRunId } },
    ),
    excludeRepository: (repository, expectedRepositoryInstanceId, reason) => write(
      "repositories",
      op.exclude_repository.operationId,
      { body: reason === undefined
        ? { repository, expected_repository_instance_id: expectedRepositoryInstanceId }
        : { repository, expected_repository_instance_id: expectedRepositoryInstanceId, reason } },
    ),
    includeRepository: (repository, expectedRepositoryInstanceId) => write(
      "repositories",
      op.include_repository.operationId,
      { body: { repository, expected_repository_instance_id: expectedRepositoryInstanceId } },
    ),
    locations: (options) => readOperation(op.list_locations.operationId, {}, options),
    league: (options) => readOperation(op.league_map.operationId, {}, options),
    sharedPlans: (options) => readOperation(op.shared_plans.operationId, {}, options),
    planTemplates: (options) => readOperation(op.plan_templates.operationId, {}, options),
    sharedPlan: (planKey, options) => readOperation(
      op.shared_plan.operationId,
      { path: { plan_key: planKey } },
      options,
    ),
    inspectSharedPlanInputs: (planKey, expectedRevision, inputs = {}, options) => readOperation(
      op.inspect_shared_plan_inputs.operationId,
      { body: { plan_key: planKey, expected_revision: expectedRevision, inputs } },
      options,
    ),
    previewSharedPlanLock: (planKey, expectedRevision, inputs = {}, options) => readOperation(
      op.preview_shared_plan_lock.operationId,
      { body: { plan_key: planKey, expected_revision: expectedRevision, inputs } },
      options,
    ),
    createSharedPlanFromTemplate: (templateKey, expectedTemplateSha256) => write(
      "plans",
      op.create_shared_plan_from_template.operationId,
      { body: { template_key: templateKey, expected_template_sha256: expectedTemplateSha256 } },
    ),
    saveSharedPlan: (planKey, content, expectedRevision) => write(
      "plans",
      op.save_shared_plan.operationId,
      { path: { plan_key: planKey }, body: { content, expected_revision: expectedRevision } },
    ),
    previewSharedPlan: (planKey, expectedRevision, repositoryIds, inputs = {}, options) => readOperation(
      op.preview_shared_plan.operationId,
      { body: { plan_key: planKey, expected_revision: expectedRevision, repository_ids: repositoryIds, inputs } },
      options,
    ),
    leagueLineups: (options) => readOperation(op.league_lineups.operationId, {}, options),
    inspectLeagueLineup: (lineupKey, options) => readOperation(
      op.inspect_league_lineup.operationId,
      { path: { lineup_key: lineupKey } },
      options,
    ),
    saveLeagueLineup: (lineupKey, name, repositories, expectedRevision) => write(
      "league-lineups",
      op.save_lineup.operationId,
      { path: { lineup_key: lineupKey }, body: { name, repositories, expected_revision: expectedRevision } },
    ),
    startSharedPlanRun: (fleetRunId, planKey, repositoryIds, expectedPlanId, expectedPlanRevision, expectedExecutionDigest, repositoryInstanceIds, inputs = {}) => write(
      "fleet-run",
      op.run_shared_plan.operationId,
      {
        body: {
          fleet_run_id: fleetRunId,
          plan_key: planKey,
          expected_plan_id: expectedPlanId,
          expected_plan_revision: expectedPlanRevision,
          expected_execution_digest: expectedExecutionDigest,
          repository_ids: repositoryIds,
          repository_instance_ids: repositoryInstanceIds,
          inputs,
        },
      },
    ),
    startTeamRun: (fleetRunId, repository, expectedPipelineSha256, expectedRepositoryInstanceId, inputs = {}) => write(
      "fleet-run",
      op.run_team.operationId,
      { body: { fleet_run_id: fleetRunId, repository, expected_pipeline_sha256: expectedPipelineSha256, expected_repository_instance_id: expectedRepositoryInstanceId, inputs } },
    ),
    browse: (path, options) => readOperation(
      op.browse.operationId,
      { query: path === undefined ? {} : { path } },
      options,
    ),
    addLocation: (path) => write(
      "locations",
      op.add_location.operationId,
      { body: { path } },
    ),
    removeLocation: (path, expectedRevision) => write(
      "locations",
      op.remove_location.operationId,
      { body: { path, expected_revision: expectedRevision } },
    ),
    recoveryStatus: (options) => readOperation(op.recovery_status.operationId, {}, options),
    reconcileEffect: (operationId, expectedRecordVersion) => write(
      "recovery",
      op.reconcile_effect.operationId,
      { path: { operation_id: operationId }, body: { expected_record_version: expectedRecordVersion } },
    ),
    acknowledgeEffect: (operationId, note, expectedRecordVersion) => write(
      "recovery",
      op.acknowledge_effect.operationId,
      { path: { operation_id: operationId }, body: { note, expected_record_version: expectedRecordVersion } },
    ),
  };
}
