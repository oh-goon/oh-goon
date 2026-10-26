import type { ApiClient, ReadRequestOptions } from "@/domain/api/ApiClient";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import { createIncludedFleet, createScaleFleet, createScaleLeague, createScaleRepositories, scaleDependencyMatches as scaleDependencyFixtureMatches } from "./BrowserScaleFixture";
import { FixtureRunRegistry } from "./fixtures/FixtureRunRegistry";
import { dependencyMatches, emptyDependencyOverview } from "./fixtures/dependencies";
import { fixtureLockDriftDiagnostic } from "./fixtures/diagnostics";
import { defaultFailureGroup, requireFailureGroup, scaleFailureGroups, scaleFailureRunId } from "./fixtures/failures";
import { fixtureCampaignReadback, fixtureCampaignResultForId, fixtureFailureHistory, fixtureLiveOutput, fixtureRunDiff } from "./fixtures/gameTape";
import { fixtureObservationWindows } from "./fixtures/executionTimeline";
import {
  FIXTURE_REPOSITORIES,
  FIXTURE_REPOSITORY_LIST,
  FixtureRepositoryRegistry,
  LONG_GOON_NAME,
  LONG_PLAN_NAME,
  LONG_STEP_NAME,
  PRIMARY_REPOSITORY,
  fixtureRepositoryInstanceId,
} from "./fixtures/identities";
import { scenarioDefinition, SHARED_RUN_SUBMIT_RELEASE_EVENT, type BrowserFixtureScenario } from "./fixtures/scenarios";
import { FIXTURE_FLEET_PLAN_TARGET, FIXTURE_PLAN_ID as PLAN_ID, FIXTURE_PLANS, FIXTURE_RESOLVED_PLAN, FIXTURE_SHA as SHA } from "./fixtures/plans";
import { DEFAULT_RUN_ID, FAILED_RUN_ID, SCALE_FAILED_RUN_ID, SCALE_RUN_ID } from "./fixtures/runIds";
import { FIXTURE_DIRECT_EXECUTION, runDetail, runState } from "./fixtures/runProjection";
import { fixtureScheduleDefinition, fixtureScheduleStatus, requireScheduleDefinition } from "./fixtures/schedules";
import {
  scoutComparisonEnvironment,
  scoutGoonRow,
  scoutPlanSummary,
  scoutRepositoryRow,
  scoutRunSample,
  scoutStepSummary,
  scoutWindow,
} from "./fixtures/scout";
import { FIXTURE_FINISHED as FINISHED, FIXTURE_NOW as NOW } from "./fixtures/time";
import { InMemoryApiClient } from "@test/InMemoryApiClient";
import {
  FleetRunEventType,
  FleetRunOutcome,
  FleetRunStatus,
  GameTapeStatus,
  ScheduleExecutionStatus,
} from "@generated/enums";
import type {
  FleetExecutionTimeline,
  FleetRunDetail,
  FleetRunOverview,
  FleetFailureGroupPage,
  FleetFailureInventory,
  FleetFailureRepositoryItem,
  FleetFailureRepositoryPage,
  FleetRunEventBatch,
  FleetRunHistoryPage,
  FleetRunState,
  FleetPlanTarget,
  DependencyAdvisoryOverview,
  DependencyCoverageRepositoryPage,
  DependencyFleetCoverageOverview,
  DependencyPackageOverview,
  DependencyRepositoryMatch,
  DependencyRepositoryPage,
  DependencySearchResult,
  DependencyTargetFilter,
  DependencyTargetMaterialization,
  DependencyTargetMaterializeRequest,
  RepositoryDependencyPage,
  LeagueLineupCatalog,
  LeagueMap,
  PlanTemplateCatalog,
  RecoveryStatus,
  RepositoryIdentity,
  ScheduleCatalog,
  ScheduleDefinition,
  ScheduleDeleteRequest,
  ScheduleOccurrence,
  ScoutFinding,
  ScoutFindingsPage,
  ScoutGoonDetail,
  ScoutGoonLeadersPage,
  ScoutHealth,
  ScoutMetricDictionary,
  ScoutOverview,
  ScoutPlanDetail,
  ScoutRepositoryDetail,
  ScoutSearchPage,
  ScheduleReplace,
  SharedPlanCatalog,
  SharedPlanDocument,
  SharedPlanInputInspection,
  SharedPlanLockPreview,
  SharedPlanPreview,
  SharedPlanScheduleCreateRequest,
} from "@generated/models";

const repositories = FIXTURE_REPOSITORY_LIST;
const repositoryRegistry = new FixtureRepositoryRegistry(repositories);
const DEFAULT_RUN_REPOSITORIES = [FIXTURE_REPOSITORIES.payments, FIXTURE_REPOSITORIES.web] as const;

const scaleRepositories = createScaleRepositories();
const scaleLeague = createScaleLeague(scaleRepositories, SHA);
const scaleFleet = createScaleFleet(scaleRepositories, NOW);
const scaleRepositoryRegistry = new FixtureRepositoryRegistry(scaleRepositories);
function requireScaleRepositoryAt(index: number): RepositoryIdentity {
  const repository = scaleRepositories[index];
  if (repository === undefined) throw new Error(`Unknown scale fixture repository position ${index}`);
  return repository;
}

const league: LeagueMap = {
  folders: ["/workspace"],
  summary: { folders: 1, git_repositories: repositories.length, configured_teams: repositories.length, missing_goon_yml: 0, invalid_goon_yml: 0 },
  teams: repositories.map((item) => ({
    repository: item,
    repository_instance_id: fixtureRepositoryInstanceId(item.repository_id),
    pipeline: { state: "CONFIGURED", path: "goon.yml", sha256: SHA, byte_count: 2048 },
  })),
};

const plans = FIXTURE_PLANS.catalog();

const SCALE_FAILURE_REPOSITORY_INDEX = 92;

function normalizedDependencyFilter(filter: DependencyTargetFilter): DependencyTargetFilter {
  return {
    package_name: filter.package_name,
    coverage: filter.coverage ?? "matching",
    resolved_version: filter.resolved_version ?? null,
    version_prefix: filter.version_prefix ?? null,
    evidence: filter.evidence ?? "all",
    version_specifier: filter.version_specifier ?? null,
    advisory_id: filter.advisory_id ?? null,
  };
}

function dependencyPopulationId(filter: DependencyTargetFilter, repoIds: readonly string[]): string {
  return JSON.stringify({ filter: normalizedDependencyFilter(filter), repo_ids: [...repoIds].sort() });
}

export class BrowserFixtureApiClient extends InMemoryApiClient {
  private revision = 200;
  private schedulesState: ScheduleCatalog;
  private readonly runs = new FixtureRunRegistry();
  private readonly dependencySelections = new Map<string, DependencyTargetMaterialization>();
  private readonly scenario: BrowserFixtureScenario;
  private readonly scenarioConfig: ReturnType<typeof scenarioDefinition>;

  constructor(scenario: BrowserFixtureScenario = "default") {
    super(scenario === "scale" ? {
      locations: ["/workspace/services"],
      fleet: scaleFleet,
      league: scaleLeague,
    } : {
      locations: scenario === "empty" ? [] : ["/workspace"],
      fleet: scenario === "empty" ? undefined : createIncludedFleet(repositories, NOW),
      league: scenario === "empty" ? { folders: [], teams: [], summary: { folders: 0, git_repositories: 0, configured_teams: 0, missing_goon_yml: 0, invalid_goon_yml: 0 } } : league,
    });
    this.scenario = scenario;
    this.scenarioConfig = scenarioDefinition(scenario);
    this.seedFixtureRuns();
    this.schedulesState = scenario === "empty" ? { schedules: [] } : { schedules: [fixtureScheduleStatus()] };
  }

  override async fleetRunOverview(): Promise<FleetRunOverview> {
    if (this.scenario === "scale") return { journal_revision: 5000, total: 5000, successful: 4312, failed: 188, running: 47, queued: 453, cancelling: 0, cancelled: 0, attention_required: 0 };
    return this.scenario === "empty"
      ? { journal_revision: 1, total: 0, successful: 0, failed: 0, running: 0, queued: 0, cancelling: 0, cancelled: 0, attention_required: 0 }
      : { journal_revision: 203, total: 2, successful: 1, failed: 1, running: 0, queued: 0, cancelling: 0, cancelled: 0, attention_required: 0 };
  }

  override async fleetRunExecutionObservations(
    query: Parameters<ApiClient["fleetRunExecutionObservations"]>[0],
  ): Promise<FleetExecutionTimeline> {
    const windows = fixtureObservationWindows(query.from_at, query.to_at, query.bucket ?? "day");
    const candidates = this.scenario === "scale" ? scaleRepositories : this.scenario === "empty" ? [] : repositories;
    const normalizedRepository = query.repository_query?.trim().toLowerCase() ?? "";
    const normalizedPlan = query.plan_query?.trim().toLowerCase() ?? "";
    const planMatches = !normalizedPlan || `${LONG_PLAN_NAME} ${PLAN_ID}`.toLowerCase().includes(normalizedPlan);
    const filtered = planMatches ? candidates.filter((item) => !normalizedRepository || `${item.name} ${item.repository_id}`.toLowerCase().includes(normalizedRepository)) : [];
    const offset = query.offset ?? 0;
    const limit = query.limit ?? 100;
    const window = windows.find((item) => item.start_at <= NOW && NOW < item.end_at) ?? windows.at(-1);
    return {
      journal_revision: this.scenario === "scale" ? 5000 : 203,
      from_at: query.from_at,
      to_at: query.to_at,
      bucket: query.bucket ?? "day",
      total_repositories: filtered.length,
      windows,
      offset,
      limit,
      items: filtered.slice(offset, offset + limit).map((repositoryItem, index) => {
        const absolute = offset + index;
        const failed = this.scenario === "scale" ? absolute % 10 === 0 : repositoryItem.repository_id === PRIMARY_REPOSITORY.repository_id;
        return {
          repository_id: repositoryItem.repository_id,
          repository_name: repositoryItem.name,
          buckets: window ? [{ start_at: window.start_at, end_at: window.end_at, run_count: 1, statuses: [{ status: failed ? GameTapeStatus.FAILED : GameTapeStatus.PASSED, count: 1 }] }] : [],
        };
      }),
    };
  }

  override async fleetFailureInventory(): Promise<FleetFailureInventory> {
    if (this.scenario === "empty") return { journal_revision: 1, failing_repositories: 0, failure_groups: 0 };
    if (this.scenario === "scale") return { journal_revision: 5000, failing_repositories: scaleRepositories.length, failure_groups: 121 };
    return { journal_revision: 203, failing_repositories: 1, failure_groups: 1 };
  }

  override async fleetFailureGroups(query?: string, offset = 0, limit = 50): Promise<FleetFailureGroupPage> {
    const groups = this.scenario === "scale" ? scaleFailureGroups(scaleRepositories.length) : this.scenario === "empty" ? [] : [defaultFailureGroup()];
    const normalized = query?.trim().toLowerCase() ?? "";
    const filtered = normalized ? groups.filter((group) => [group.plan_name, group.plan_id, group.goon_name ?? "", group.step_id ?? ""].join(" ").toLowerCase().includes(normalized)) : groups;
    return { journal_revision: this.scenario === "scale" ? 5000 : 203, total: filtered.length, offset, limit, items: filtered.slice(offset, offset + limit) };
  }

  override async fleetFailureRepositories(groupId: string, query?: string, signatureId?: string, offset = 0, limit = 50): Promise<FleetFailureRepositoryPage> {
    const groups = this.scenario === "scale" ? scaleFailureGroups(scaleRepositories.length) : [defaultFailureGroup()];
    const group = requireFailureGroup(groups, groupId);
    const candidates = this.scenario === "scale" && group.group_id === "failure-group-scale-000" ? scaleRepositories : [PRIMARY_REPOSITORY];
    const normalized = query?.trim().toLowerCase() ?? "";
    const items: FleetFailureRepositoryItem[] = candidates.map((repositoryItem, index) => {
      const runId = this.scenario === "scale" ? scaleFailureRunId(index) : FAILED_RUN_ID;
      if (this.scenario === "scale") this.ensureTerminalRun(runId, [repositoryItem], FIXTURE_FLEET_PLAN_TARGET, "failed");
      return {
      repository: repositoryItem,
      fleet_run_id: runId,
      group_id: group.group_id,
      plan_id: group.plan_id,
      plan_name: group.plan_name,
      goon_name: group.goon_name,
      step_id: group.step_id,
      signature_id: this.scenario === "scale" ? `signature-scale-${index % 4}` : "signature-default-failure",
      failure_code: "OPERATION_FAILED",
      failure_message: "Step returned a non-zero exit code.",
    };
    }).filter((item) => (!normalized || `${item.repository.name} ${item.repository.path} ${item.repository.repository_id}`.toLowerCase().includes(normalized)) && (!signatureId || item.signature_id === signatureId));
    return { journal_revision: this.scenario === "scale" ? 5000 : 203, group, total: items.length, offset, limit, items: items.slice(offset, offset + limit) };
  }

  override async scoutOverview(hours = 24, _limit = 20, _options?: ReadRequestOptions): Promise<ScoutOverview> {
    const window = scoutWindow(hours);
    const heavy = scoutRepositoryRow();
    return {
      enabled: true, journal_revision: 212, window, executions: 41820, active_repositories: 3281, failure_rate_percent: 4.1,
      totals: { total_cpu_seconds: 42631200, cpu_coverage: { observed: 41780, eligible: 41820, percent: 99.9 }, peak_memory_bytes: 5153960755, memory_coverage: { observed: 38120, eligible: 41820, percent: 91.2 }, memory_time_available: false, memory_time_gib_seconds: null },
      needs_scouting: [
        { category: "HEAVY_HITTER", count: 8, explanation: "Highest total observed CPU consumption" },
        { category: "RUN_SPIKE", count: 12, explanation: "Repositories running much more often than their own historical baseline" },
        { category: "GOON_REGRESSION", count: 2, explanation: "Goon source changes associated with increased CPU/run" },
        { category: "CPU_OUTLIER", count: 14, explanation: "CPU-intensive Steps relative to genuinely comparable peers" },
        { category: "MEMORY_OUTLIER", count: 17, explanation: "High peak-memory Steps relative to genuinely comparable peers" },
        { category: "FAILURE_WASTE", count: 11, explanation: "Observed CPU consumed by unsuccessful work" },
      ],
      heavy_hitter_total: 25, goon_leader_total: 84,
      heavy_hitters: [heavy, { ...heavy, key: "repo-web", repository_id: "repo-web", display_name: "customer-portal-web", total_cpu_seconds: 1008000, run_count: 4191, median_cpu_seconds_per_run: 240 }],
      goon_leaders: [scoutGoonRow()],
      trend: Array.from({ length: 12 }, (_, index) => ({ start_at: new Date(Date.parse(window.start_at) + index * 2 * 60 * 60 * 1000).toISOString(), end_at: new Date(Date.parse(window.start_at) + (index + 1) * 2 * 60 * 60 * 1000).toISOString(), run_count: 2600 + index * 90, total_cpu_seconds: 2800000 + index * 120000, peak_memory_bytes: 2500000000 + index * 120000000, cpu_coverage: { observed: 100, eligible: 100, percent: 100 }, memory_coverage: { observed: 98, eligible: 100, percent: 98 } })),
    };
  }

  override async scoutFindings(hours = 24, category?: ScoutFinding["category"], offset = 0, limit = 20, _options?: ReadRequestOptions): Promise<ScoutFindingsPage> {
    const primaryRepository = PRIMARY_REPOSITORY;
    const finding: ScoutFinding = { category: category ?? "CPU_OUTLIER", entity_kind: "STEP", repository_id: primaryRepository.repository_id, repository_name: primaryRepository.name, goon_name: LONG_GOON_NAME, goon_source_sha256: SHA, baseline_goon_source_sha256: "b".repeat(64), current_period_start_at: "2026-08-15T10:00:00Z", current_period_end_at: NOW, baseline_period_start_at: "2026-08-08T10:00:00Z", baseline_period_end_at: "2026-08-15T09:59:00Z", step_id: LONG_STEP_NAME, metric: "cpu_per_run", unit: "CPU-s", observed: 121, baseline: 42, ratio: 2.88, sample_count: 944, peer_repository_count: 1821, coverage: { observed: 943, eligible: 944, percent: 99.9 }, comparison_environment: scoutComparisonEnvironment(), explanation: "Median CPU/run is 2.9× the peer median for the same Goon source, Step and compatible execution environment. Scout reports the measured difference, not its cause.", representative_run_id: DEFAULT_RUN_ID };
    const population = category === "HEAVY_HITTER"
      ? Array.from({ length: 25 }, (_, index) => { const identity = requireScaleRepositoryAt(index); return { ...finding, category: "HEAVY_HITTER" as const, entity_kind: "REPOSITORY" as const, repository_id: identity.repository_id, repository_name: identity.name, goon_name: null, goon_source_sha256: null, step_id: null, metric: "total_cpu" as const, observed: 1_500_000 - index * 1000, baseline: null, ratio: null, explanation: "Highest total observed CPU consumption in the selected Scout window." }; })
      : [finding];
    return { enabled: true, journal_revision: 212, window: scoutWindow(hours), category: category ?? null, total: population.length, offset, limit, items: population.slice(offset, offset + limit) };
  }

  override async scoutRepository(...args: Parameters<ApiClient["scoutRepository"]>): Promise<ScoutRepositoryDetail> {
    const [repositoryId, hours = 24, planId, section, offset = 0, limit = 100] = args;
    const repositoryIdentity = this.repositoryForScenario(repositoryId);
    const repositoryRow = { ...scoutRepositoryRow(), key: repositoryId, repository_id: repositoryId, display_name: repositoryIdentity.name };
    const plans = section === "plans" ? Array.from({ length: Math.max(0, Math.min(limit, 37 - offset)) }, (_, index) => ({ ...scoutPlanSummary(), plan_id: `plan-${String(offset + index + 1).padStart(3, "0")}`, plan_name: `Plan ${String(offset + index + 1).padStart(3, "0")}` })) : [scoutPlanSummary()];
    const goons = section === "goons" ? Array.from({ length: Math.max(0, Math.min(limit, 28 - offset)) }, (_, index) => ({ ...scoutGoonRow(), key: `repo-goon-${offset + index + 1}`, display_name: `${LONG_GOON_NAME}-${offset + index + 1}`, goon_name: `${LONG_GOON_NAME}-${offset + index + 1}` })) : [scoutGoonRow()];
    const steps = section === "steps" ? Array.from({ length: Math.max(0, Math.min(limit, 84 - offset)) }, (_, index) => ({ ...scoutStepSummary(), step_id: `${LONG_STEP_NAME}-${offset + index + 1}` })) : [scoutStepSummary()];
    return { enabled: true, journal_revision: 212, window: scoutWindow(hours), repository: repositoryRow, selected_plan_id: planId ?? null, plan_total: 37, goon_total: 28, step_total: 84, page_section: section ?? null, page_offset: section ? offset : 0, page_limit: limit, plans, goons, steps, representative_runs: [scoutRunSample()] };
  }

  override async scoutPlan(...args: Parameters<ApiClient["scoutPlan"]>): Promise<ScoutPlanDetail> {
    const [planId, hours = 24, section, offset = 0, limit = 100] = args;
    const repositoriesPage = section === "repositories" ? Array.from({ length: Math.max(0, Math.min(limit, 5000 - offset)) }, (_, index) => {
      const position = offset + index;
      const identity = requireScaleRepositoryAt(position);
      return { ...scoutRepositoryRow(), key: identity.repository_id, repository_id: identity.repository_id, display_name: identity.name, total_cpu_seconds: Math.max(1, 1_504_800 - position) };
    }) : [scoutRepositoryRow()];
    const goons = section === "goons" ? Array.from({ length: Math.max(0, Math.min(limit, 42 - offset)) }, (_, index) => ({ ...scoutGoonRow(), key: `plan-goon-${offset + index + 1}`, display_name: `${LONG_GOON_NAME}-${offset + index + 1}`, goon_name: `${LONG_GOON_NAME}-${offset + index + 1}` })) : [scoutGoonRow()];
    const steps = section === "steps" ? Array.from({ length: Math.max(0, Math.min(limit, 118 - offset)) }, (_, index) => ({ ...scoutStepSummary(), step_id: `${LONG_STEP_NAME}-${offset + index + 1}` })) : [scoutStepSummary()];
    return { enabled: true, journal_revision: 212, window: scoutWindow(hours), plan_id: planId, plan_name: LONG_PLAN_NAME, repository_total: 5000, goon_total: 42, step_total: 118, page_section: section ?? null, page_offset: section ? offset : 0, page_limit: limit, repositories: repositoriesPage, goons, steps, representative_runs: [scoutRunSample()] };
  }

  override async scoutSearch(query: string, hours = 24, offset = 0, limit = 20, _options?: ReadRequestOptions): Promise<ScoutSearchPage> {
    const normalized = query.trim().toLowerCase();
    const items = [
      { entity_kind: "REPOSITORY" as const, key: PRIMARY_REPOSITORY.repository_id, display_name: PRIMARY_REPOSITORY.name, repository_id: PRIMARY_REPOSITORY.repository_id },
      { entity_kind: "PLAN" as const, key: PLAN_ID, display_name: LONG_PLAN_NAME, plan_id: PLAN_ID },
      { entity_kind: "GOON" as const, key: LONG_GOON_NAME, display_name: LONG_GOON_NAME, goon_name: LONG_GOON_NAME, goon_source_sha256: SHA },
    ].filter((item) => item.display_name.toLowerCase().startsWith(normalized) || item.key.toLowerCase().startsWith(normalized));
    const pageItems = items.slice(offset, offset + limit);
    return { enabled: true, journal_revision: 212, window: scoutWindow(hours), query: query.trim(), total: items.length, offset, limit, items: pageItems };
  }

  override async scoutGoonLeaders(hours = 24, offset = 0, limit = 20, _options?: ReadRequestOptions): Promise<ScoutGoonLeadersPage> {
    const rows = Array.from({ length: 25 }, (_, index) => ({ ...scoutGoonRow(), key: `goon-${index + 1}`, display_name: `${LONG_GOON_NAME}-${index + 1}`, goon_name: `${LONG_GOON_NAME}-${index + 1}`, total_cpu_seconds: 1072800 - index * 1000 }));
    return { enabled: true, journal_revision: 212, window: scoutWindow(hours), total: rows.length, offset, limit, items: rows.slice(offset, offset + limit) };
  }

  override async scoutGoon(...args: Parameters<ApiClient["scoutGoon"]>): Promise<ScoutGoonDetail> {
    const [name, sourceSha256, hours = 24, section, offset = 0, limit = 100] = args;
    const version = (position: number) => ({ goon_name: name, source_sha256: position === 0 ? SHA : position === 1 ? "b".repeat(64) : position.toString(16).padStart(64, "0"), first_seen_at: "2026-08-08T10:00:00Z", last_seen_at: NOW, repository_count: Math.max(1, 1219 - position), run_count: Math.max(1, 6144 - position), total_cpu_seconds: Math.max(1, 1072800 - position * 1000), median_cpu_seconds_per_run: 121, cpu_coverage: { observed: 100, eligible: 100, percent: 100 } });
    const versions = section === "versions" ? Array.from({ length: Math.max(0, Math.min(limit, 12 - offset)) }, (_, index) => version(offset + index)) : [version(0), version(1)];
    const repositoriesPage = section === "repositories" ? Array.from({ length: Math.max(0, Math.min(limit, 1219 - offset)) }, (_, index) => { const position = offset + index; const identity = requireScaleRepositoryAt(position); return { ...scoutRepositoryRow(), key: identity.repository_id, repository_id: identity.repository_id, display_name: identity.name }; }) : [scoutRepositoryRow()];
    const steps = section === "steps" ? Array.from({ length: Math.max(0, Math.min(limit, 31 - offset)) }, (_, index) => ({ ...scoutStepSummary(), step_id: `${LONG_STEP_NAME}-${offset + index + 1}` })) : [scoutStepSummary()];
    return { enabled: true, journal_revision: 212, window: scoutWindow(hours), goon_name: name, selected_source_sha256: sourceSha256 ?? null, version_total: 12, repository_total: 1219, step_total: 31, page_section: section ?? null, page_offset: section ? offset : 0, page_limit: limit, versions, repositories: repositoriesPage, steps, representative_runs: [scoutRunSample()] };
  }

  override async scoutMetrics(_options?: ReadRequestOptions): Promise<ScoutMetricDictionary> {
    return { measurement_schema_version: 3, definitions: [{ metric: "total_cpu", display_name: "Total CPU", definition: "Accumulated observed CPU execution time.", unit: "CPU-seconds", source: "Physical attempt resource evidence", aggregation: "Sum observed CPU time", missing_data: "Missing observations remain missing", comparison_rule: "Ranking only; peer comparisons use compatible cohorts" }] };
  }

  override async scoutHealth(_options?: ReadRequestOptions): Promise<ScoutHealth> {
    return { enabled: true, measurement_schema_version: 3, journal_revision: 212, processed_journal_revision: 212, lag_revisions: 0, retained_run_observations: 41820, retained_step_observations: 128440, snapshot_cache_entries: 4, snapshot_cache_hits: 983, snapshot_cache_misses: 17, analytical_storage_bytes: 12_582_912, detail_retention_days: 90, run_retention_days: 365 };
  }

  override async dependencySearch(...args: Parameters<ApiClient["dependencySearch"]>): Promise<DependencySearchResult> {
    const [query, offset = 0, limit = 20] = args;
    const normalized = query.trim().toLowerCase();
    const items = normalized && "mlflow".includes(normalized)
      ? [{ ecosystem: "pypi" as const, package_name: "mlflow", repositories: this.scenario === "scale" ? 137 : 5, resolved_versions: this.scenario === "scale" ? 2 : 4 }]
      : this.scenario === "scale" && normalized && "package".startsWith(normalized)
        ? Array.from({ length: 65 }, (_, index) => ({ ecosystem: "pypi" as const, package_name: `package-${String(index + 1).padStart(3, "0")}`, repositories: 1 + (index % 50), resolved_versions: 1 + (index % 4) }))
        : [];
    return { query: query.trim(), total: items.length, offset, limit, items: items.slice(offset, offset + limit) };
  }

  override async dependencyFleetCoverage(_options?: ReadRequestOptions): Promise<DependencyFleetCoverageOverview> {
    if (this.scenario === "scale") return { repository_total: 5000, current_dependency_evidence: 4848, unknown_dependency_evidence: 0, stale_dependency_evidence: 0, missing_dependency_evidence: 152, no_current_dependency_evidence: 152 };
    return { repository_total: 7, current_dependency_evidence: 5, unknown_dependency_evidence: 1, stale_dependency_evidence: 0, missing_dependency_evidence: 1, no_current_dependency_evidence: 2 };
  }

  override async dependencyOverview(packageName: string, _options?: ReadRequestOptions): Promise<DependencyPackageOverview> {
    if (packageName.toLowerCase() !== "mlflow") return emptyDependencyOverview(packageName, repositories);
    if (this.scenario === "scale") return {
      ecosystem: "pypi", package_name: "mlflow", observed_repositories: 137, latest_observed_at: NOW,
      freshness: { current: 137, no_matching_dependency: 4711, unknown: 0, stale: 0, no_current_evidence: 152 },
      versions: [{ version: "2.17.2", repositories: 92 }, { version: "2.18.0", repositories: 45 }],
    };
    return {
      ecosystem: "pypi",
      package_name: "mlflow",
      observed_repositories: 5,
      latest_observed_at: NOW,
      freshness: { current: 4, no_matching_dependency: 1, unknown: 1, stale: 0, no_current_evidence: 2 },
      versions: [
        { version: "2.17.2", repositories: 1 },
        { version: "2.17.1", repositories: 1 },
        { version: "2.18.0", repositories: 1 },
        { version: "2.18.0rc1", repositories: 1 },
        { version: "2.16.0", repositories: 1 },
      ],
    };
  }

  override async dependencyAdvisory(packageName: string, advisoryId: string, affectedSpecifier: string, _options?: ReadRequestOptions): Promise<DependencyAdvisoryOverview> {
    if (this.scenario === "scale") return {
      package_name: packageName, advisory_id: advisoryId, affected_specifier: affectedSpecifier,
      affected_repositories: packageName.toLowerCase() === "mlflow" && affectedSpecifier === "<2.18.0" ? 92 : 0,
      observed_not_affected: packageName.toLowerCase() === "mlflow" && affectedSpecifier === "<2.18.0" ? 45 : 0,
      version_unclassified: 0, unknown: 0, stale: 0, no_matching_dependency: packageName.toLowerCase() === "mlflow" ? 4711 : 0, no_current_evidence: packageName.toLowerCase() === "mlflow" ? 152 : 5000,
    };
    return {
      package_name: packageName,
      advisory_id: advisoryId,
      affected_specifier: affectedSpecifier,
      affected_repositories: packageName.toLowerCase() === "mlflow" && affectedSpecifier === "<2.18.0" ? 2 : 0,
      observed_not_affected: packageName.toLowerCase() === "mlflow" && affectedSpecifier === "<2.18.0" ? 1 : 0,
      version_unclassified: packageName.toLowerCase() === "mlflow" && affectedSpecifier === "<2.18.0" ? 1 : 0,
      unknown: packageName.toLowerCase() === "mlflow" ? 1 : 0,
      stale: 0,
      no_matching_dependency: packageName.toLowerCase() === "mlflow" ? 1 : 0,
      no_current_evidence: packageName.toLowerCase() === "mlflow" ? 2 : repositories.length,
    };
  }

  override async dependencyRepositories(
    packageName: string,
    resolvedVersion?: string,
    _versionPrefix?: string,
    evidence: "all" | "current" | "unknown" | "stale" = "all",
    offset = 0,
    limit = 20,
    versionSpecifier?: string,
    _advisoryId?: string,
    _options?: ReadRequestOptions,
  ): Promise<DependencyRepositoryPage> {
    let items = this.scenario === "scale" ? scaleDependencyFixtureMatches(scaleRepositories, packageName, NOW, DEFAULT_RUN_ID) : dependencyMatches(packageName);
    if (resolvedVersion) items = items.filter((item) => (item.resolved_versions ?? []).includes(resolvedVersion));
    if (evidence !== "all") items = items.filter((item) => item.evidence.freshness === evidence);
    if (versionSpecifier === "<2.18.0") items = items.filter((item) => item.evidence.freshness === "current" && ["2.17.2", "2.17.1"].some((version) => (item.resolved_versions ?? []).includes(version)));
    const filter: DependencyTargetFilter = { package_name: packageName, coverage: "matching", resolved_version: resolvedVersion ?? null, version_prefix: _versionPrefix ?? null,
      evidence, version_specifier: versionSpecifier ?? null, advisory_id: _advisoryId ?? null };
    const repoIds = items.map((item) => item.repository.repository_id);
    return { package_name: packageName, population_id: dependencyPopulationId(filter, repoIds), total: items.length, offset, limit, items: items.slice(offset, offset + limit) };
  }

  override async dependencyCoverageRepositories(
    packageName: string,
    coverage: "absent_from_lock" | "no_current_evidence",
    offset = 0,
    limit = 20,
    _options?: ReadRequestOptions,
  ): Promise<DependencyCoverageRepositoryPage> {
    if (this.scenario === "scale" && packageName.toLowerCase() === "mlflow") {
      const start = coverage === "absent_from_lock" ? 137 : 4848;
      const total = coverage === "absent_from_lock" ? 4711 : 152;
      const pageRepositories = scaleRepositories.slice(start + offset, start + Math.min(total, offset + limit));
      const items = pageRepositories.map((repositoryIdentity, index) => ({ repository: repositoryIdentity, coverage, evidence: { scope: "locked-inventory" as const, freshness: coverage === "absent_from_lock" ? "current" as const : "unknown" as const, completeness: coverage === "absent_from_lock" ? "complete" as const : "unknown" as const, availability: coverage === "absent_from_lock" ? "available" as const : "missing" as const, absence_admissible: coverage === "absent_from_lock" }, sources: coverage === "absent_from_lock" ? ["uv.lock"] : [], observed_at: coverage === "absent_from_lock" ? NOW : undefined, fleet_run_id: coverage === "absent_from_lock" ? DEFAULT_RUN_ID : undefined, operation_id: coverage === "absent_from_lock" ? `operation-scale-coverage-${offset + index}` : undefined, producer_goon: coverage === "absent_from_lock" ? "python/dependency-inventory" : undefined, step_id: coverage === "absent_from_lock" ? "dependency-inventory" : undefined }));
      const filter: DependencyTargetFilter = { package_name: packageName, coverage, evidence: "all" };
      const repoIds = scaleRepositories.slice(start, start + total).map((item) => item.repository_id);
      return { package_name: packageName, coverage, population_id: dependencyPopulationId(filter, repoIds), total, offset, limit, items };
    }
    const items = packageName.toLowerCase() !== "mlflow" ? [] : coverage === "absent_from_lock"
      ? [{ repository: FIXTURE_REPOSITORIES.notify, coverage, evidence: { freshness: "current" as const, completeness: "complete" as const, availability: "available" as const, absence_admissible: true }, sources: ["uv.lock"], observed_at: NOW, fleet_run_id: DEFAULT_RUN_ID, operation_id: "operation-dependency-notify", producer_goon: "python/dependency-inventory", step_id: "dependency-inventory" }]
      : [
          { repository: FIXTURE_REPOSITORIES.catalog, coverage, evidence: { freshness: "unknown" as const, completeness: "unknown" as const, availability: "available" as const, absence_admissible: false }, sources: ["uv.lock"], observed_at: "2026-08-10T12:00:00Z", fleet_run_id: DEFAULT_RUN_ID, operation_id: "operation-dependency-catalog", producer_goon: "python/dependency-inventory", step_id: "dependency-inventory" },
          { repository: FIXTURE_REPOSITORIES.report, coverage, evidence: { freshness: "unknown" as const, completeness: "unknown" as const, availability: "missing" as const, absence_admissible: false }, sources: [] },
        ];
    const filter: DependencyTargetFilter = { package_name: packageName, coverage, evidence: "all" };
    const repoIds = items.map((item) => item.repository.repository_id);
    return { package_name: packageName, coverage, population_id: dependencyPopulationId(filter, repoIds), total: items.length, offset, limit, items: items.slice(offset, offset + limit) };
  }

  override async repositoryDependencies(repositoryId: string, offset = 0, limit = 20, _options?: ReadRequestOptions): Promise<RepositoryDependencyPage> {
    const repositoryIdentity = this.repositoryForScenario(repositoryId);
    const items = this.scenario === "scale"
      ? Array.from({ length: 240 }, (_, index): DependencyRepositoryMatch => ({
          repository: repositoryIdentity,
          ecosystem: "pypi",
          package_name: `scale-package-${String(index + 1).padStart(3, "0")}`,
          resolved_versions: [`${1 + (index % 5)}.${index % 20}.0`],
          contexts: ["lock"],
          evidence: { freshness: "current", completeness: "complete", availability: "available", absence_admissible: true },
          sources: ["uv.lock"],
          observed_at: NOW,
          fleet_run_id: DEFAULT_RUN_ID,
          operation_id: `operation-scale-repository-dependency-${index + 1}`,
          producer_goon: "python/dependency-inventory",
          step_id: "dependency-inventory",
        }))
      : dependencyMatches("mlflow").filter((item) => item.repository.repository_id === repositoryId);
    return { repository: repositoryIdentity, total: items.length, offset, limit, items: items.slice(offset, offset + limit) };
  }

  override async materializeDependencyTargets(request: DependencyTargetMaterializeRequest): Promise<WriteCommandResult<DependencyTargetMaterialization>> {
    const filter = request.filter;
    const repoIds = this.scenario === "scale"
      ? filter.coverage === "no_current_evidence"
        ? scaleRepositories.slice(4848).map((item) => item.repository_id)
        : filter.coverage === "absent_from_lock"
          ? scaleRepositories.slice(137, 4848).map((item) => item.repository_id)
          : filter.version_specifier === "<2.18.0"
            ? scaleRepositories.slice(0, 92).map((item) => item.repository_id)
            : scaleRepositories.slice(0, 137).map((item) => item.repository_id)
      : filter.coverage === "no_current_evidence"
      ? [FIXTURE_REPOSITORIES.catalog.repository_id, FIXTURE_REPOSITORIES.report.repository_id]
      : filter.coverage === "absent_from_lock"
        ? [FIXTURE_REPOSITORIES.notify.repository_id]
        : filter.version_specifier === "<2.18.0"
          ? [FIXTURE_REPOSITORIES.payments.repository_id, FIXTURE_REPOSITORIES.web.repository_id]
          : dependencyMatches(filter.package_name).map((item) => item.repository.repository_id);
    if (dependencyPopulationId(filter, repoIds) !== request.expected_population_id) {
      return { outcome: "stale", diagnosticId: "fixture:dependency-population" };
    }
    const repositoryInstanceIds = Object.fromEntries(repoIds.map((repositoryId) => [repositoryId, fixtureRepositoryInstanceId(repositoryId)]));
    const materialization: DependencyTargetMaterialization = {
      query_id: request.expected_population_id,
      filter,
      repo_ids: repoIds,
      repository_instance_ids: repositoryInstanceIds,
      total: repoIds.length,
      selected_at: NOW,
    };
    this.dependencySelections.set(request.expected_population_id, clone(materialization));
    return this.fixtureSaved(materialization);
  }

  override async dependencyTargetSelection(queryId: string, _options?: ReadRequestOptions): Promise<DependencyTargetMaterialization> {
    const selection = this.dependencySelections.get(queryId);
    if (!selection) throw new Error(`Unknown dependency selection ${queryId}`);
    return clone(selection);
  }

  override async sharedPlans(): Promise<SharedPlanCatalog> {
    return clone(this.scenario === "empty" ? { plans: [] } : plans);
  }

  override async planTemplates(): Promise<PlanTemplateCatalog> {
    return clone(FIXTURE_PLANS.templates());
  }

  override async sharedPlan(planKey: string): Promise<SharedPlanDocument> {
    return clone(FIXTURE_PLANS.document(planKey));
  }

  override async createSharedPlanFromTemplate(templateKey: string, _expectedTemplateSha256: string): Promise<WriteCommandResult<SharedPlanDocument>> {
    const planKey = FIXTURE_PLANS.planKeyForTemplate(templateKey);
    return this.fixtureSaved(FIXTURE_PLANS.document(planKey));
  }

  /** Scripted browser presentation fixture only; real constraints belong to FleetPlanResolver. */
  override async inspectSharedPlanInputs(
    planKey: string, expectedRevision: string, inputs: Record<string, string> = {},
    _options?: ReadRequestOptions,
  ): Promise<SharedPlanInputInspection> {
    const document = FIXTURE_PLANS.document(planKey);
    if (document.sha256 !== expectedRevision) throw new Error("Fixture Shared Plan revision is stale");
    if (planKey === "python-quality" && inputs.coverage_threshold === "101") {
      return {
        schema: "oh-goon.shared-plan-input-inspection/v1",
        status: "rejected", plan_key: null, sha256: null, plan_id: null,
        diagnostic: {
          schema: "oh-goon.plan-validation-diagnostic/v1", status: "rejected",
          stage: "input_contract", code: "INVALID_INPUT_VALUE", inputs: ["coverage_threshold"],
          safe_next_action: "Inspect the owning Goon's type and constraints; correct the caller input value.",
          execution: "not_started_by_diagnostic",
        },
        execution: "not_started_by_inspection",
      };
    }
    return {
      schema: "oh-goon.shared-plan-input-inspection/v1",
      status: "accepted", plan_key: planKey, sha256: expectedRevision,
      plan_id: FIXTURE_PLANS.target(planKey).plan.plan_id,
      diagnostic: null, execution: "not_started_by_inspection",
    };
  }

  override async previewSharedPlanLock(planKey: string, expectedRevision: string, _inputs: Record<string, string> = {}, _options?: ReadRequestOptions): Promise<SharedPlanLockPreview> {
    const document = FIXTURE_PLANS.document(planKey);
    if (document.sha256 !== expectedRevision) throw new Error("Fixture Shared Plan revision is stale");
    const target = FIXTURE_PLANS.target(planKey);
    return {
      plan_key: planKey,
      plan_id: target.plan.plan_id,
      sha256: expectedRevision,
      execution_digest: target.identity.digest,
      content: `# Generated by Oh Goon. Do not edit manually.\nlock_version: 2\nplan: sha256:${"b".repeat(64)}\nexecution:\n- goon: python/quality\n  digest: sha256:${"c".repeat(64)}\ndigest: sha256:${expectedRevision}\n`,
    };
  }

  override async leagueLineups(): Promise<LeagueLineupCatalog> {
    return { lineups: [] };
  }

  override async previewSharedPlan(planKey: string, expectedRevision: string, repositoryIds: string[], inputs: Record<string, string> = {}, _options?: ReadRequestOptions): Promise<SharedPlanPreview> {
    const document = FIXTURE_PLANS.document(planKey);
    if (document.sha256 !== expectedRevision) throw new Error("Fixture Shared Plan revision is stale");
    const target = FIXTURE_PLANS.target(planKey);
    const blockedRepositoryRole = this.scenarioConfig.blockedRepositoryRole;
    const blockedRepositoryId = blockedRepositoryRole === undefined
      ? null
      : FIXTURE_REPOSITORIES[blockedRepositoryRole].repository_id;
    const teams: SharedPlanPreview["teams"] = repositoryIds.map((id) => {
      const repositoryIdentity = this.repositoryForScenario(id);
      return id === blockedRepositoryId
        ? { state: "BLOCKED", repository: repositoryIdentity, repository_instance_id: fixtureRepositoryInstanceId(repositoryIdentity.repository_id), reason: "goon.lock does not match the resolved execution identity", diagnostic: fixtureLockDriftDiagnostic() }
        : {
            state: "READY",
            repository: repositoryIdentity,
            repository_instance_id: fixtureRepositoryInstanceId(repositoryIdentity.repository_id),
            preview: {
              plan_id: target.plan.plan_id,
              repository: repositoryIdentity.path,
              steps: FIXTURE_RESOLVED_PLAN.steps.map((step) => ({ step_id: step.step_id, kind: step.kind ?? "command", disposition: "READY" })),
            },
          };
    });
    return {
      plan_key: planKey,
      plan_id: target.plan.plan_id,
      sha256: target.source.kind === "shared_plan" ? target.source.sha256 : SHA,
      execution_digest: target.identity.digest,
      explicit_inputs: Object.entries(inputs).map(([name, value]) => ({ name, value })),
      ready_count: teams.filter((entry) => entry.state === "READY").length,
      blocked_count: teams.filter((entry) => entry.state === "BLOCKED").length,
      teams,
    };
  }

  override async schedules(): Promise<ScheduleCatalog> {
    return clone(this.schedulesState);
  }

  override async createScheduleFromSharedPlan(scheduleId: string, request: SharedPlanScheduleCreateRequest): Promise<WriteCommandResult<ScheduleDefinition>> {
    const planDocument = await this.sharedPlan(request.plan_key);
    if (request.expected_plan_revision !== planDocument.sha256) {
      throw new Error(`Shared Plan ${request.plan_key} changed after review`);
    }
    request.repository_ids.forEach((repositoryId) => this.repositoryForScenario(repositoryId));
    const definition = fixtureScheduleDefinition(scheduleId, request.name, request.repository_ids, request.interval_minutes, request.state, FIXTURE_PLANS.target(request.plan_key));
    const schedules = this.schedulesState.schedules ?? [];
    this.schedulesState = { schedules: [{ definition, latest_occurrence: null, next_run_at: "2026-08-22T16:00:00Z", missing_expected_occurrence_at: null }, ...schedules] };
    return this.fixtureSaved(definition);
  }

  override async updateSchedule(scheduleId: string, schedule: ScheduleReplace): Promise<WriteCommandResult<ScheduleDefinition>> {
    const schedules = this.schedulesState.schedules ?? [];
    const current = requireScheduleDefinition(schedules, scheduleId);
    if (current.generation_id !== schedule.generation_id || current.record_version !== schedule.record_version) {
      return { outcome: "stale", diagnosticId: `fixture:schedule-authority:${scheduleId}` };
    }
    const definition: ScheduleDefinition = { ...current, ...schedule, schedule_id: scheduleId, updated_at: FINISHED, record_version: current.record_version + 1 };
    this.schedulesState = { schedules: schedules.map((item) => item.definition.schedule_id === scheduleId ? { ...item, definition } : item) };
    return this.fixtureSaved(definition);
  }

  override async deleteSchedule(scheduleId: string, request: ScheduleDeleteRequest): Promise<WriteCommandResult<void>> {
    const schedules = this.schedulesState.schedules ?? [];
    const current = requireScheduleDefinition(schedules, scheduleId);
    if (current.generation_id !== request.generation_id || current.record_version !== request.record_version) {
      return { outcome: "stale", diagnosticId: `fixture:schedule-authority:${scheduleId}` };
    }
    this.schedulesState = { schedules: schedules.filter((item) => item.definition.schedule_id !== scheduleId) };
    return this.fixtureSaved(undefined);
  }

  override async startSchedule(
    scheduleId: string,
    scheduleRunId: string,
    expectedGenerationId: string,
    expectedRecordVersion: number,
  ): Promise<WriteCommandResult<ScheduleOccurrence>> {
    const schedules = this.schedulesState.schedules ?? [];
    const definition = requireScheduleDefinition(schedules, scheduleId);
    if (definition.generation_id !== expectedGenerationId || definition.record_version !== expectedRecordVersion) {
      return { outcome: "stale", diagnosticId: `fixture:schedule-authority:${scheduleId}` };
    }
    const expected = definition.repository_ids.length;
    const occurrence: ScheduleOccurrence = {
      schedule_run_id: scheduleRunId, schedule_id: scheduleId, status: ScheduleExecutionStatus.RUNNING, started_at: NOW,
      expected, pending: expected, running: 0, successful: 0, attention_required: 0, failed: 0, cancelled: 0, not_started: 0, attention_count: 0,
    };
    this.schedulesState = { schedules: schedules.map((item) => item.definition.schedule_id === scheduleId ? { ...item, latest_occurrence: occurrence } : item) };
    return this.fixtureSaved(occurrence);
  }

  override async fleetRunHistory(
    _beforeRevision?: number,
    limit = 12,
    outcome?: string,
    status?: string,
    _source?: string,
    repositoryId?: string,
    fromAt?: string,
    toAt?: string,
    planQuery?: string,
    _options?: ReadRequestOptions,
  ): Promise<FleetRunHistoryPage> {
    if (this.scenario === "empty") return { journal_revision: 1, items: [], next_before_revision: null };
    const sourceItems = this.scenario === "scale"
      ? [
          { admitted_revision: 5001, execution: FIXTURE_DIRECT_EXECUTION, run: this.projectRunState(SCALE_RUN_ID), outcome: FleetRunOutcome.SUCCESS },
          { admitted_revision: 5000, execution: FIXTURE_DIRECT_EXECUTION, run: this.projectRunState(SCALE_FAILED_RUN_ID), outcome: FleetRunOutcome.FAILED },
        ]
      : [
          { admitted_revision: 203, execution: FIXTURE_DIRECT_EXECUTION, run: this.projectRunState(DEFAULT_RUN_ID), outcome: FleetRunOutcome.SUCCESS },
          { admitted_revision: 202, execution: FIXTURE_DIRECT_EXECUTION, run: this.projectRunState(FAILED_RUN_ID), outcome: FleetRunOutcome.FAILED },
        ];
    const items = sourceItems.filter((item) => {
      if (outcome && item.outcome !== outcome) return false;
      if (status && item.run.status !== status) return false;
      if (repositoryId && !item.run.targets.some((target) => target.repository_id === repositoryId)) return false;
      if (fromAt && item.run.accepted_at < fromAt) return false;
      if (toAt && item.run.accepted_at >= toAt) return false;
      if (planQuery) {
        const plan = item.run.target?.kind === "fleet_plan" ? item.run.target.plan : null;
        if (!plan || !`${plan.name} ${plan.plan_id}`.toLowerCase().includes(planQuery.toLowerCase())) return false;
      }
      return true;
    }).slice(0, limit);
    return { journal_revision: this.scenario === "scale" ? 5001 : 203, next_before_revision: null, items };
  }

  override async agentCampaign(campaignId: string) { return fixtureCampaignResultForId(campaignId); }

  override async fleetRunDetail(runId: string): Promise<FleetRunDetail> {
    const run = this.projectRunState(runId);
    const failed = run.status === FleetRunStatus.FAILED;
    const detail = runDetail(
      run,
      run.status === FleetRunStatus.RUNNING ? GameTapeStatus.RUNNING : failed ? GameTapeStatus.FAILED : GameTapeStatus.PASSED,
      failed,
    );
    if (this.scenarioConfig.campaignDecision) {
      const campaign = fixtureCampaignReadback(runId, this.scenarioConfig.campaignDecision === "converged", this.scenarioConfig.campaignDecision === "not_admitted");
      detail.campaign_journal = campaign;
      detail.game_tape.campaign_journal = campaign;
      for (const repository of detail.game_tape.repositories) {
        for (const step of repository.steps ?? []) {
          step.settlement = { owner: "fleet_step_settlement", outcome: "SUCCESS", cleanup: "VERIFIED", reason: "ACCEPTED_EXIT", observed_returncode: 0 };
        }
      }
    }
    return detail;
  }

  override async compareFleetRuns(runId: string, baselineRunId: string) {
    return fixtureRunDiff(runId, baselineRunId);
  }

  override async gameTapeFailureHistory(runId: string, repositoryId: string, stepId: string) {
    return fixtureFailureHistory(runId, repositoryId, stepId);
  }

  override async gameTapeLiveOutput(runId: string, after = 0) {
    return fixtureLiveOutput(runId, after);
  }

  override async startTeamRun(fleetRunId: string, repositoryPath: string, expectedPipelineSha256: string, expectedRepositoryInstanceId: string): Promise<WriteCommandResult<FleetRunState>> {
    const teams = (this.scenario === "scale" ? scaleLeague.teams : league.teams) ?? [];
    const displayed = teams.find((team) => team.repository.path === repositoryPath);
    const expectedSha = displayed?.pipeline.state === "CONFIGURED" ? displayed.pipeline.sha256 : undefined;
    if (!expectedSha || expectedPipelineSha256 !== expectedSha) return { outcome: "stale", diagnosticId: `fixture:team-pipeline:${repositoryPath}` };
    if (!displayed || expectedRepositoryInstanceId !== displayed.repository_instance_id) return { outcome: "stale", diagnosticId: `fixture:team-repository-instance:${repositoryPath}` };
    const target = this.scenario === "scale" ? scaleRepositoryRegistry.requireByPath(repositoryPath) : repositoryRegistry.requireByPath(repositoryPath);
    const planTarget = FIXTURE_FLEET_PLAN_TARGET;
    this.runs.register({ runId: fleetRunId, targets: [target], planTarget, terminalAfterEventReads: 3 });
    return this.fixtureSaved(runState(fleetRunId, FleetRunStatus.RUNNING, [target], 1, 0, planTarget));
  }

  override async startSharedPlanRun(fleetRunId: string, planKey: string, repositoryIds: string[], expectedPlanId: string, expectedPlanRevision: string, expectedExecutionDigest: string, repositoryInstanceIds: Record<string, string>, _inputs: Record<string, string> = {}): Promise<WriteCommandResult<FleetRunState>> {
    if (this.scenarioConfig.holdSubmit === true) await waitForFixtureEvent(SHARED_RUN_SUBMIT_RELEASE_EVENT);
    if (this.scenarioConfig.submitFailureDiagnosticId !== undefined) return { outcome: "write-error", diagnosticId: this.scenarioConfig.submitFailureDiagnosticId, recovery: "refresh" };
    const targets = repositoryIds.map((id) => this.repositoryForScenario(id));
    if (Object.keys(repositoryInstanceIds).length !== repositoryIds.length || repositoryIds.some((id) => repositoryInstanceIds[id] !== fixtureRepositoryInstanceId(id))) {
      return { outcome: "stale", diagnosticId: "fixture:shared-plan-repository-instance" };
    }
    const planTarget = FIXTURE_PLANS.target(planKey);
    const planDocument = FIXTURE_PLANS.document(planKey);
    if (
      planTarget.plan.plan_id !== expectedPlanId
      || planDocument.sha256 !== expectedPlanRevision
      || planTarget.identity.digest !== expectedExecutionDigest
    ) {
      return { outcome: "stale", diagnosticId: `fixture:shared-plan:${planKey}` };
    }
    this.runs.register({
      runId: fleetRunId,
      targets,
      planTarget,
      terminalOutcome: this.scenarioConfig.terminalOutcome ?? "passed",
      terminalAfterEventReads: 3,
    });
    return this.fixtureSaved(runState(fleetRunId, FleetRunStatus.RUNNING, targets, targets.length, 0, planTarget));
  }

  override async fleetRunEvents(runId: string, after: number, _options?: ReadRequestOptions): Promise<FleetRunEventBatch> {
    const record = this.runs.advanceEventRead(runId);
    const terminal = this.runs.isTerminal(record);
    if (!terminal) {
      return {
        run: runState(runId, FleetRunStatus.RUNNING, [...record.targets], record.targets.length, 0, record.planTarget),
        events: after === 0
          ? [{ sequence: 1, event_type: FleetRunEventType.RUN_STARTED, fleet_run_id: runId, generated_at: NOW }]
          : [],
        next_sequence: 1,
        journal_revision: this.scenario === "scale" ? 5002 : 204,
      };
    }
    const failed = record.terminalOutcome === "failed";
    const terminalRun = runState(
      runId,
      failed ? FleetRunStatus.FAILED : FleetRunStatus.COMPLETED,
      [...record.targets],
      record.targets.length,
      record.targets.length,
      record.planTarget,
    );
    const terminalEvent: FleetRunEventBatch["events"][number] = failed
      ? {
          sequence: 2,
          event_type: FleetRunEventType.RUN_FAILED,
          fleet_run_id: runId,
          generated_at: FINISHED,
          failure: terminalRun.status === FleetRunStatus.FAILED
            ? terminalRun.failure
            : { stage: "EXECUTION", code: "FIXTURE_LONG_FAILURE", kind: "INTERNAL", retryable: false, message: "Fixture Run failed." },
        }
      : {
          sequence: 2,
          event_type: FleetRunEventType.RUN_COMPLETED,
          fleet_run_id: runId,
          generated_at: FINISHED,
        };
    return { run: terminalRun, events: [terminalEvent], next_sequence: 2, journal_revision: this.scenario === "scale" ? 5003 : 205 };
  }

  override async rerunFleetRun(runId: string, newFleetRunId: string): Promise<WriteCommandResult<FleetRunState>> {
    const source = this.projectRunState(runId);
    if (source.target?.kind !== "fleet_plan") throw new Error(`Fixture Run ${runId} has no FleetPlanTarget to rerun`);
    const targets = [...source.targets];
    this.runs.register({ runId: newFleetRunId, targets, planTarget: source.target, terminalAfterEventReads: 3 });
    return this.fixtureSaved(runState(newFleetRunId, FleetRunStatus.RUNNING, targets, targets.length, 0, source.target));
  }

  override async cancelFleetRun(runId: string): Promise<WriteCommandResult<FleetRunState>> {
    const record = this.runs.require(runId);
    return this.fixtureSaved(runState(runId, FleetRunStatus.CANCELLED, [...record.targets], record.targets.length, 0, record.planTarget));
  }

  override async recoveryStatus(): Promise<RecoveryStatus> {
    return { effect_quarantines: [], repair_plan: { actions: [], findings: [] }, runtime_safety: { status: "PROVEN_SAFE", checked_at: NOW, durable_revision: 1, journal_revision: 1, findings: [], effects_allowed: true }, invariants: { durable_revision: 1, findings: [], healthy: true, journal_revision: 1 } };
  }

  private repositoryForScenario(repositoryId: string): RepositoryIdentity {
    return this.scenario === "scale"
      ? scaleRepositoryRegistry.requireById(repositoryId)
      : repositoryRegistry.requireById(repositoryId);
  }

  private seedFixtureRuns(): void {
    this.runs.register({ runId: DEFAULT_RUN_ID, targets: DEFAULT_RUN_REPOSITORIES, planTarget: FIXTURE_FLEET_PLAN_TARGET });
    this.runs.register({ runId: FAILED_RUN_ID, targets: [PRIMARY_REPOSITORY], planTarget: FIXTURE_FLEET_PLAN_TARGET, terminalOutcome: "failed" });
    this.runs.register({ runId: SCALE_RUN_ID, targets: scaleRepositories, planTarget: FIXTURE_FLEET_PLAN_TARGET });
    this.runs.register({ runId: SCALE_FAILED_RUN_ID, targets: [requireScaleRepositoryAt(SCALE_FAILURE_REPOSITORY_INDEX)], planTarget: FIXTURE_FLEET_PLAN_TARGET, terminalOutcome: "failed" });
  }

  private ensureTerminalRun(
    runId: string,
    targets: readonly RepositoryIdentity[],
    planTarget: FleetPlanTarget,
    terminalOutcome: "passed" | "failed",
  ): void {
    if (this.runs.has(runId)) return;
    this.runs.register({ runId, targets, planTarget, terminalOutcome });
  }

  private projectRunState(runId: string): FleetRunState {
    const record = this.runs.require(runId);
    const terminal = this.runs.isTerminal(record);
    const failed = terminal && record.terminalOutcome === "failed";
    return runState(
      runId,
      failed ? FleetRunStatus.FAILED : terminal ? FleetRunStatus.COMPLETED : FleetRunStatus.RUNNING,
      [...record.targets],
      record.targets.length,
      terminal ? record.targets.length : 0,
      record.planTarget,
    );
  }

  private fixtureSaved<Value>(value: Value): WriteCommandResult<Value> {
    this.revision += 1;
    return { outcome: "saved", value, causalityRevision: this.revision };
  }
}

function clone<Value>(value: Value): Value {
  return structuredClone(value);
}

function waitForFixtureEvent(eventName: string): Promise<void> {
  return new Promise((resolve) => {
    window.addEventListener(eventName, () => resolve(), { once: true });
  });
}
