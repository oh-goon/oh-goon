import type {
  operations as op,
  OperationCall,
  OperationName,
  OperationResponse,
} from "@generated/operations";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

type OperationBody<Name extends OperationName> = NonNullable<OperationCall<Name>["body"]>;
type ReadOperationResult<Name extends OperationName> = Promise<OperationResponse<Name>>;
type WriteOperationResult<Name extends OperationName> = Promise<WriteCommandResult<OperationResponse<Name>>>;
type FleetRunRequest = OperationBody<typeof op.start_fleet_run.operationId>;
type FleetRunHistoryOutcome = NonNullable<NonNullable<OperationCall<typeof op.fleet_run_history.operationId>["query"]>["outcome"]>;
type FleetRunHistorySource = NonNullable<NonNullable<OperationCall<typeof op.fleet_run_history.operationId>["query"]>["source"]>;
type FleetRunHistoryStatus = NonNullable<NonNullable<OperationCall<typeof op.fleet_run_history.operationId>["query"]>["status"]>;
type FleetRunExecutionObservationQuery = NonNullable<OperationCall<typeof op.fleet_run_execution_observations.operationId>["query"]>;
type ScoutFindingCategory = NonNullable<NonNullable<OperationCall<typeof op.scout_findings.operationId>["query"]>["category"]>;
type ScoutDetailSection = NonNullable<NonNullable<OperationCall<typeof op.scout_plan.operationId>["query"]>["section"]>;
type ScheduleOccurrenceStatus = NonNullable<NonNullable<OperationCall<typeof op.schedule_occurrence_repositories.operationId>["query"]>["status"]>;

export interface ReadRequestOptions {
  readonly signal?: AbortSignal;
}

export interface FleetRunOptions {
  target?: FleetRunRequest["target"];
  repositoryIds?: FleetRunRequest["repository_ids"];
  repositoryInstanceIds?: FleetRunRequest["repository_instance_ids"];
}

export interface ApiClient {
  health(options?: ReadRequestOptions): ReadOperationResult<typeof op.health.operationId>;
  privacy(options?: ReadRequestOptions): ReadOperationResult<typeof op.privacy_status.operationId>;
  fleet(options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_snapshot.operationId>;
  scoutOverview(hours?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.scout_overview.operationId>;
  scoutFindings(hours?: number, category?: ScoutFindingCategory, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.scout_findings.operationId>;
  scoutRepository(repositoryId: string, hours?: number, planId?: string, section?: ScoutDetailSection, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.scout_repository.operationId>;
  scoutPlan(planId: string, hours?: number, section?: ScoutDetailSection, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.scout_plan.operationId>;
  scoutGoon(name: string, sourceSha256?: string, hours?: number, section?: ScoutDetailSection, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.scout_goon.operationId>;
  scoutGoonLeaders(hours?: number, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.scout_goon_leaders.operationId>;
  scoutSearch(query: string, hours?: number, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.scout_search.operationId>;
  scoutMetrics(options?: ReadRequestOptions): ReadOperationResult<typeof op.scout_metric_dictionary.operationId>;
  scoutHealth(options?: ReadRequestOptions): ReadOperationResult<typeof op.scout_health.operationId>;
  scoutRun(runId: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.scout_run.operationId>;
  fleetRunOverview(options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_run_overview.operationId>;
  fleetRunExecutionObservations(query: FleetRunExecutionObservationQuery, options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_run_execution_observations.operationId>;
  fleetFailureInventory(options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_failure_inventory.operationId>;
  fleetFailureGroups(query?: string, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_failure_groups.operationId>;
  fleetFailureRepositories(groupId: string, query?: string, signatureId?: string, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_failure_repositories.operationId>;
  fleetRunHistory(beforeRevision?: number, limit?: number, outcome?: FleetRunHistoryOutcome, status?: FleetRunHistoryStatus, source?: FleetRunHistorySource, repositoryId?: string, fromAt?: string, toAt?: string, planQuery?: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_run_history.operationId>;
  fleetRunDetail(runId: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.get_fleet_run.operationId>;
  compareFleetRuns(runId: string, baselineRunId: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.compare_fleet_runs.operationId>;
  gameTapePerformance(runId: string, sampleLimit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.game_tape_performance.operationId>;
  gameTapeFailureHistory(runId: string, repositoryId: string, stepId: string, sampleLimit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.game_tape_failure_history.operationId>;
  gameTapeLiveOutput(runId: string, after?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.game_tape_live_output.operationId>;
  dependencySearch(query: string, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.dependency_search.operationId>;
  dependencyFleetCoverage(options?: ReadRequestOptions): ReadOperationResult<typeof op.dependency_fleet_coverage.operationId>;
  dependencyOverview(packageName: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.dependency_overview.operationId>;
  dependencyAdvisory(packageName: string, advisoryId: string, affectedSpecifier: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.dependency_advisory_overview.operationId>;
  dependencyCoverageRepositories(packageName: string, coverage: "proven_no_match" | "no_current_evidence", offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.dependency_coverage_repositories.operationId>;
  dependencyRepositories(packageName: string, resolvedVersion?: string, versionPrefix?: string, relationship?: "all" | "direct" | "transitive", evidence?: "all" | "current" | "aged" | "stale", offset?: number, limit?: number, versionSpecifier?: string, advisoryId?: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.dependency_repositories.operationId>;
  repositoryDependencies(repositoryId: string, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.repository_dependencies.operationId>;
  materializeDependencyTargets(request: OperationBody<typeof op.materialize_dependency_targets.operationId>): WriteOperationResult<typeof op.materialize_dependency_targets.operationId>;
  dependencyTargetSelection(queryId: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.dependency_target_selection.operationId>;
  schedules(options?: ReadRequestOptions): ReadOperationResult<typeof op.schedule_catalog.operationId>;
  createScheduleFromSharedPlan(scheduleId: string, schedule: OperationBody<typeof op.create_schedule_from_shared_plan.operationId>): WriteOperationResult<typeof op.create_schedule_from_shared_plan.operationId>;
  updateSchedule(scheduleId: string, schedule: OperationBody<typeof op.replace_schedule.operationId>): WriteOperationResult<typeof op.replace_schedule.operationId>;
  deleteSchedule(scheduleId: string, request: OperationBody<typeof op.delete_schedule.operationId>): WriteOperationResult<typeof op.delete_schedule.operationId>;
  startSchedule(
    scheduleId: string,
    scheduleRunId: string,
    expectedGenerationId: string,
    expectedRecordVersion: number,
  ): WriteOperationResult<typeof op.start_schedule_run.operationId>;
  scheduleOccurrenceRepositories(scheduleId: string, scheduleRunId: string, status?: ScheduleOccurrenceStatus, offset?: number, limit?: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.schedule_occurrence_repositories.operationId>;
  fleetCommands(options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_command_center.operationId>;
  executorStatus(options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_executor_status.operationId>;
  placementQueue(options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_placement_queue.operationId>;
  drainExecutor(executorId: string, expectedGeneration: number, expectedOperationalRevision: number): WriteOperationResult<typeof op.drain_executor.operationId>;
  activateExecutor(executorId: string, expectedGeneration: number, expectedOperationalRevision: number): WriteOperationResult<typeof op.activate_executor.operationId>;
  revokeExecutorTrust(executorId: string, expectedGeneration: number, expectedOperationalRevision: number): WriteOperationResult<typeof op.revoke_executor_trust.operationId>;
  restoreExecutorTrust(executorId: string, expectedGeneration: number, expectedOperationalRevision: number): WriteOperationResult<typeof op.restore_executor_trust.operationId>;
  cancelQueuedAdmission(runId: string, expectedSequence: number): WriteOperationResult<typeof op.cancel_queued_admission.operationId>;
  fleetSecurity(options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_security.operationId>;
  adoptUvPreview(options?: ReadRequestOptions): ReadOperationResult<typeof op.adopt_uv_preview.operationId>;
  startFleetRun(fleetRunId: string, action: FleetRunRequest["action"], options?: FleetRunOptions): WriteOperationResult<typeof op.start_fleet_run.operationId>;
  fleetRunEvents(runId: string, after: number, options?: ReadRequestOptions): ReadOperationResult<typeof op.fleet_run_events.operationId>;
  cancelFleetRun(runId: string): WriteOperationResult<typeof op.cancel_fleet_run.operationId>;
  readmitFleetRun(runId: string, newFleetRunId: string): WriteOperationResult<typeof op.readmit_fleet_run.operationId>;
  rerunFleetRun(runId: string, newFleetRunId: string): WriteOperationResult<typeof op.rerun_fleet_run.operationId>;
  excludeRepository(repository: string, expectedRepositoryInstanceId: string, reason?: string): WriteOperationResult<typeof op.exclude_repository.operationId>;
  includeRepository(repository: string, expectedRepositoryInstanceId: string): WriteOperationResult<typeof op.include_repository.operationId>;
  locations(options?: ReadRequestOptions): ReadOperationResult<typeof op.list_locations.operationId>;
  league(options?: ReadRequestOptions): ReadOperationResult<typeof op.league_map.operationId>;
  sharedPlans(options?: ReadRequestOptions): ReadOperationResult<typeof op.shared_plans.operationId>;
  planTemplates(options?: ReadRequestOptions): ReadOperationResult<typeof op.plan_templates.operationId>;
  sharedPlan(planKey: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.shared_plan.operationId>;
  inspectSharedPlanInputs(planKey: string, expectedRevision: string, inputs?: Record<string, string>, options?: ReadRequestOptions): ReadOperationResult<typeof op.inspect_shared_plan_inputs.operationId>;
  previewSharedPlanLock(planKey: string, expectedRevision: string, inputs?: Record<string, string>, options?: ReadRequestOptions): ReadOperationResult<typeof op.preview_shared_plan_lock.operationId>;
  createSharedPlanFromTemplate(templateKey: string, expectedTemplateSha256: string): WriteOperationResult<typeof op.create_shared_plan_from_template.operationId>;
  saveSharedPlan(planKey: string, content: string, expectedRevision: string | null): WriteOperationResult<typeof op.save_shared_plan.operationId>;
  previewSharedPlan(planKey: string, expectedRevision: string, repositoryIds: string[], inputs?: Record<string, string>, options?: ReadRequestOptions): ReadOperationResult<typeof op.preview_shared_plan.operationId>;
  leagueLineups(options?: ReadRequestOptions): ReadOperationResult<typeof op.league_lineups.operationId>;
  inspectLeagueLineup(lineupKey: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.inspect_league_lineup.operationId>;
  saveLeagueLineup(lineupKey: string, name: string, repositories: string[], expectedRevision: string | null): WriteOperationResult<typeof op.save_lineup.operationId>;
  startSharedPlanRun(
    fleetRunId: string,
    planKey: string,
    repositoryIds: string[],
    expectedPlanId: string,
    expectedPlanRevision: string,
    expectedExecutionDigest: string,
    repositoryInstanceIds: Record<string, string>,
    inputs?: Record<string, string>,
  ): WriteOperationResult<typeof op.run_shared_plan.operationId>;
  startTeamRun(fleetRunId: string, repository: string, expectedPipelineSha256: string, expectedRepositoryInstanceId: string, inputs?: Record<string, string>): WriteOperationResult<typeof op.run_team.operationId>;
  browse(path?: string, options?: ReadRequestOptions): ReadOperationResult<typeof op.browse.operationId>;
  addLocation(path: string): WriteOperationResult<typeof op.add_location.operationId>;
  removeLocation(path: string, expectedRevision: string): WriteOperationResult<typeof op.remove_location.operationId>;
  recoveryStatus(options?: ReadRequestOptions): ReadOperationResult<typeof op.recovery_status.operationId>;
  reconcileEffect(operationId: string, expectedRecordVersion: number): WriteOperationResult<typeof op.reconcile_effect.operationId>;
  acknowledgeEffect(operationId: string, note: string, expectedRecordVersion: number): WriteOperationResult<typeof op.acknowledge_effect.operationId>;
}
