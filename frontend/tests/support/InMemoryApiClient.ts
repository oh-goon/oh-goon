import type { ApiClient, ReadRequestOptions } from "@/domain/api/ApiClient";
import { SystemHealthStatus } from "@generated/enums";
import type {
  BrowseResponse,
  FleetExecutionTimeline,
  FleetRunOverview,
  FleetFailureGroupPage,
  FleetFailureInventory,
  FleetFailureRepositoryPage,
  FleetSnapshot,
  LeagueMap,
  LocationsResponse,
  PrivacyResponse,
  SystemHealth,
  RecoveryStatus,
  ScoutFinding,
  ScoutFindingsPage,
  ScoutGoonLeadersPage,
  ScoutHealth,
  ScoutMetricDictionary,
  ScoutOverview,
  ScoutSearchPage,
} from "@generated/models";
import { MutationController } from "@/domain/concurrency/MutationController";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

export interface InMemoryApiState {
  fleetReads: number;
}

export interface InMemoryApiSeed {
  locations?: string[];
  unavailableLocations?: string[];
  browsePath?: string;
  fleet?: FleetSnapshot;
  league?: LeagueMap;
}

export interface DeferredFleetRead {
  readonly started: Promise<void>;
  readonly settled: Promise<"released" | "aborted">;
  release(): void;
}

type QueuedFleetRead =
  | { readonly outcome: "fixture-snapshot"; readonly snapshot: FleetSnapshot; readonly gate: FleetReadGate | null }
  | { readonly outcome: "fixture-error"; readonly error: Error; readonly gate: FleetReadGate | null };

const EMPTY_FLEET: FleetSnapshot = {
  attention: { action_required: 0, groups: [], healthy: 0 },
  provenance: {
    generated_at: "2026-08-10T12:00:00Z",
    journal_revision: 1,
    snapshot_id: "service-free-fixture",
    durable_revision: 1,
  },
  repositories: [],
  review_preparation: null,
  summary: { discovered: 0, excluded: 0, included: 0, needs_attention: 0, ready_for_update: 0 },
};

const HEALTH: SystemHealth = {
  status: SystemHealthStatus.READY,
  tools: [],
  background_failures: [],
  runtime_safety: {
    status: "PROVEN_SAFE",
    checked_at: "2026-08-10T12:00:00Z",
    durable_revision: 1,
    journal_revision: 1,
    findings: [],
    effects_allowed: true,
  },
};
const PRIVACY: PrivacyResponse = {
  local_database_file: "/tmp/oh-goon-fixture.db",
  local_database_storage: true,
  loopback_only: true,
  mode: "LOCAL_ONLY",
  persistent_repository_state: true,
  source_upload: false,
  telemetry: false,
};

export class InMemoryApiClient implements ApiClient {
  readonly state: InMemoryApiState;
  private locationsState: string[];
  private unavailableLocationsState: string[];
  private readonly browsePath: string;
  private fleetSnapshot: FleetSnapshot;
  private readonly leagueTeams: NonNullable<LeagueMap["teams"]>;
  private readonly leagueSummary: Omit<LeagueMap["summary"], "folders">;
  private readonly fleetQueue: QueuedFleetRead[] = [];
  private readonly locationWriteFailures: string[] = [];
  private causalityRevision = 0;

  constructor(seed: InMemoryApiSeed = {}, private readonly mutations?: MutationController) {
    this.state = { fleetReads: 0 };
    this.locationsState = committedSeedLocations(seed);
    this.unavailableLocationsState = (seed.unavailableLocations ?? []).filter((path) => this.locationsState.includes(path));
    this.browsePath = seed.browsePath ?? "/workspace/new";
    this.fleetSnapshot = cloneApiValue(seed.fleet ?? EMPTY_FLEET);
    this.leagueTeams = cloneApiValue(seed.league?.teams ?? []);
    const summary = seed.league?.summary;
    this.leagueSummary = {
      git_repositories: summary?.git_repositories ?? 0,
      configured_teams: summary?.configured_teams ?? 0,
      missing_goon_yml: summary?.missing_goon_yml ?? 0,
      invalid_goon_yml: summary?.invalid_goon_yml ?? 0,
    };
  }

  setCommittedFleetSnapshot(snapshot: FleetSnapshot): void {
    this.fleetSnapshot = cloneApiValue(snapshot);
  }

  queueFleetRead(snapshot: FleetSnapshot): void {
    this.fleetQueue.push({ outcome: "fixture-snapshot", snapshot: cloneApiValue(snapshot), gate: null });
  }

  deferFleetRead(snapshot: FleetSnapshot): DeferredFleetRead {
    const gate = new FleetReadGate();
    this.fleetQueue.push({ outcome: "fixture-snapshot", snapshot: cloneApiValue(snapshot), gate });
    return gate;
  }

  queueFleetFailure(message: string): void {
    this.fleetQueue.push({ outcome: "fixture-error", error: new Error(message), gate: null });
  }

  deferFleetFailure(message: string): DeferredFleetRead {
    const gate = new FleetReadGate();
    this.fleetQueue.push({ outcome: "fixture-error", error: new Error(message), gate });
    return gate;
  }

  queueLocationWriteFailure(message: string): void {
    this.locationWriteFailures.push(message);
  }

  async health(options?: ReadRequestOptions): Promise<SystemHealth> {
    throwIfAborted(options?.signal);
    return cloneApiValue(HEALTH);
  }

  async privacy(options?: ReadRequestOptions): Promise<PrivacyResponse> {
    throwIfAborted(options?.signal);
    return cloneApiValue(PRIVACY);
  }

  async fleet(options?: ReadRequestOptions): Promise<FleetSnapshot> {
    this.state.fleetReads += 1;
    const queued = this.fleetQueue.shift();
    if (queued !== undefined) {
      if (queued.gate !== null) await queued.gate.wait(options?.signal);
      else throwIfAborted(options?.signal);
      if (queued.outcome === "fixture-error") throw queued.error;
      return cloneApiValue(queued.snapshot);
    }
    throwIfAborted(options?.signal);
    return cloneApiValue(this.fleetSnapshot);
  }

  async scoutOverview(hours = 24, _limit = 20, _options?: ReadRequestOptions): Promise<ScoutOverview> {
    return {
      enabled: true, journal_revision: 1, window: scoutWindow(hours), executions: 0, active_repositories: 0, failure_rate_percent: 0,
      totals: { total_cpu_seconds: 0, cpu_coverage: { observed: 0, eligible: 0, percent: 100 }, peak_memory_bytes: null, memory_coverage: { observed: 0, eligible: 0, percent: 100 }, memory_time_available: false, memory_time_gib_seconds: null },
      needs_scouting: [], heavy_hitter_total: 0, goon_leader_total: 0, heavy_hitters: [], goon_leaders: [], trend: [],
    };
  }
  async scoutFindings(hours = 24, category?: ScoutFinding["category"], offset = 0, limit = 20, _options?: ReadRequestOptions): Promise<ScoutFindingsPage> {
    return { enabled: true, journal_revision: 1, window: scoutWindow(hours), category: category ?? null, total: 0, offset, limit, items: [] };
  }
  scoutRepository(..._args: Parameters<ApiClient["scoutRepository"]>): ReturnType<ApiClient["scoutRepository"]> { return unsupported("scoutRepository"); }
  scoutPlan(..._args: Parameters<ApiClient["scoutPlan"]>): ReturnType<ApiClient["scoutPlan"]> { return unsupported("scoutPlan"); }
  scoutGoon(..._args: Parameters<ApiClient["scoutGoon"]>): ReturnType<ApiClient["scoutGoon"]> { return unsupported("scoutGoon"); }
  async scoutGoonLeaders(hours = 24, offset = 0, limit = 20, _options?: ReadRequestOptions): Promise<ScoutGoonLeadersPage> { return { enabled: true, journal_revision: 1, window: scoutWindow(hours), total: 0, offset, limit, items: [] }; }
  async scoutSearch(query: string, hours = 24, offset = 0, limit = 20, _options?: ReadRequestOptions): Promise<ScoutSearchPage> { return { enabled: true, journal_revision: 1, window: scoutWindow(hours), query, total: 0, offset, limit, items: [] }; }
  async scoutMetrics(_options?: ReadRequestOptions): Promise<ScoutMetricDictionary> { return { measurement_schema_version: 3, definitions: [{ metric: "total_cpu", display_name: "Total CPU", definition: "Observed accumulated processor time.", unit: "CPU-seconds", source: "Physical attempt resource evidence", aggregation: "Sum", missing_data: "Missing remains missing", comparison_rule: "Additive rankings only" }] }; }
  scoutRun(..._args: Parameters<ApiClient["scoutRun"]>): ReturnType<ApiClient["scoutRun"]> { return unsupported("scoutRun"); }
  async scoutHealth(_options?: ReadRequestOptions): Promise<ScoutHealth> {
    return {
      enabled: true,
      measurement_schema_version: 3,
      journal_revision: 1,
      processed_journal_revision: 1,
      lag_revisions: 0,
      retained_run_observations: 0,
      retained_step_observations: 0,
      snapshot_cache_entries: 0,
      snapshot_cache_hits: 0,
      snapshot_cache_misses: 0,
      analytical_storage_bytes: 0,
      detail_retention_days: 90,
      run_retention_days: 365,
    };
  }
  async fleetRunOverview(): Promise<FleetRunOverview> { return { journal_revision: 1, total: 0, successful: 0, failed: 0, running: 0, queued: 0, cancelling: 0, cancelled: 0, attention_required: 0 }; }
  async fleetRunExecutionObservations(query: Parameters<ApiClient["fleetRunExecutionObservations"]>[0]): Promise<FleetExecutionTimeline> { return { journal_revision: 1, from_at: query.from_at, to_at: query.to_at, bucket: query.bucket ?? "day", total_repositories: 0, windows: [{ start_at: query.from_at, end_at: query.to_at }], offset: query.offset ?? 0, limit: query.limit ?? 100, items: [] }; }
  async fleetFailureInventory(): Promise<FleetFailureInventory> { return { journal_revision: 1, failing_repositories: 0, failure_groups: 0 }; }
  async fleetFailureGroups(_query?: string, offset = 0, limit = 50): Promise<FleetFailureGroupPage> { return { journal_revision: 1, total: 0, offset, limit, items: [] }; }
  fleetFailureRepositories(..._args: Parameters<ApiClient["fleetFailureRepositories"]>): Promise<FleetFailureRepositoryPage> { return unsupported("fleetFailureRepositories"); }
  fleetRunHistory(..._args: Parameters<ApiClient["fleetRunHistory"]>): ReturnType<ApiClient["fleetRunHistory"]> { return unsupported("fleetRunHistory"); }
  fleetRunDetail(..._args: Parameters<ApiClient["fleetRunDetail"]>): ReturnType<ApiClient["fleetRunDetail"]> { return unsupported("fleetRunDetail"); }
  compareFleetRuns(..._args: Parameters<ApiClient["compareFleetRuns"]>): ReturnType<ApiClient["compareFleetRuns"]> { return unsupported("compareFleetRuns"); }
  gameTapePerformance(..._args: Parameters<ApiClient["gameTapePerformance"]>): ReturnType<ApiClient["gameTapePerformance"]> { return unsupported("gameTapePerformance"); }
  gameTapeFailureHistory(..._args: Parameters<ApiClient["gameTapeFailureHistory"]>): ReturnType<ApiClient["gameTapeFailureHistory"]> { return unsupported("gameTapeFailureHistory"); }
  gameTapeLiveOutput(..._args: Parameters<ApiClient["gameTapeLiveOutput"]>): ReturnType<ApiClient["gameTapeLiveOutput"]> { return unsupported("gameTapeLiveOutput"); }
  dependencySearch(..._args: Parameters<ApiClient["dependencySearch"]>): ReturnType<ApiClient["dependencySearch"]> { return unsupported("dependencySearch"); }
  dependencyFleetCoverage(..._args: Parameters<ApiClient["dependencyFleetCoverage"]>): ReturnType<ApiClient["dependencyFleetCoverage"]> { return unsupported("dependencyFleetCoverage"); }
  dependencyOverview(..._args: Parameters<ApiClient["dependencyOverview"]>): ReturnType<ApiClient["dependencyOverview"]> { return unsupported("dependencyOverview"); }
  dependencyAdvisory(..._args: Parameters<ApiClient["dependencyAdvisory"]>): ReturnType<ApiClient["dependencyAdvisory"]> { return unsupported("dependencyAdvisory"); }
  dependencyCoverageRepositories(..._args: Parameters<ApiClient["dependencyCoverageRepositories"]>): ReturnType<ApiClient["dependencyCoverageRepositories"]> { return unsupported("dependencyCoverageRepositories"); }
  dependencyRepositories(..._args: Parameters<ApiClient["dependencyRepositories"]>): ReturnType<ApiClient["dependencyRepositories"]> { return unsupported("dependencyRepositories"); }
  repositoryDependencies(..._args: Parameters<ApiClient["repositoryDependencies"]>): ReturnType<ApiClient["repositoryDependencies"]> { return unsupported("repositoryDependencies"); }
  materializeDependencyTargets(..._args: Parameters<ApiClient["materializeDependencyTargets"]>): ReturnType<ApiClient["materializeDependencyTargets"]> { return unsupported("materializeDependencyTargets"); }
  dependencyTargetSelection(..._args: Parameters<ApiClient["dependencyTargetSelection"]>): ReturnType<ApiClient["dependencyTargetSelection"]> { return unsupported("dependencyTargetSelection"); }
  schedules(..._args: Parameters<ApiClient["schedules"]>): ReturnType<ApiClient["schedules"]> { return unsupported("schedules"); }
  createScheduleFromSharedPlan(..._args: Parameters<ApiClient["createScheduleFromSharedPlan"]>): ReturnType<ApiClient["createScheduleFromSharedPlan"]> { return unsupported("createScheduleFromSharedPlan"); }
  updateSchedule(..._args: Parameters<ApiClient["updateSchedule"]>): ReturnType<ApiClient["updateSchedule"]> { return unsupported("updateSchedule"); }
  deleteSchedule(..._args: Parameters<ApiClient["deleteSchedule"]>): ReturnType<ApiClient["deleteSchedule"]> { return unsupported("deleteSchedule"); }
  startSchedule(..._args: Parameters<ApiClient["startSchedule"]>): ReturnType<ApiClient["startSchedule"]> { return unsupported("startSchedule"); }
  scheduleOccurrenceRepositories(..._args: Parameters<ApiClient["scheduleOccurrenceRepositories"]>): ReturnType<ApiClient["scheduleOccurrenceRepositories"]> { return unsupported("scheduleOccurrenceRepositories"); }
  fleetCommands(..._args: Parameters<ApiClient["fleetCommands"]>): ReturnType<ApiClient["fleetCommands"]> { return unsupported("fleetCommands"); }
  executorStatus(..._args: Parameters<ApiClient["executorStatus"]>): ReturnType<ApiClient["executorStatus"]> { return unsupported("executorStatus"); }
  placementQueue(..._args: Parameters<ApiClient["placementQueue"]>): ReturnType<ApiClient["placementQueue"]> { return unsupported("placementQueue"); }
  drainExecutor(..._args: Parameters<ApiClient["drainExecutor"]>): ReturnType<ApiClient["drainExecutor"]> { return unsupported("drainExecutor"); }
  activateExecutor(..._args: Parameters<ApiClient["activateExecutor"]>): ReturnType<ApiClient["activateExecutor"]> { return unsupported("activateExecutor"); }
  revokeExecutorTrust(..._args: Parameters<ApiClient["revokeExecutorTrust"]>): ReturnType<ApiClient["revokeExecutorTrust"]> { return unsupported("revokeExecutorTrust"); }
  restoreExecutorTrust(..._args: Parameters<ApiClient["restoreExecutorTrust"]>): ReturnType<ApiClient["restoreExecutorTrust"]> { return unsupported("restoreExecutorTrust"); }
  cancelQueuedAdmission(..._args: Parameters<ApiClient["cancelQueuedAdmission"]>): ReturnType<ApiClient["cancelQueuedAdmission"]> { return unsupported("cancelQueuedAdmission"); }
  fleetSecurity(..._args: Parameters<ApiClient["fleetSecurity"]>): ReturnType<ApiClient["fleetSecurity"]> { return unsupported("fleetSecurity"); }
  adoptUvPreview(..._args: Parameters<ApiClient["adoptUvPreview"]>): ReturnType<ApiClient["adoptUvPreview"]> { return unsupported("adoptUvPreview"); }
  startFleetRun(..._args: Parameters<ApiClient["startFleetRun"]>): ReturnType<ApiClient["startFleetRun"]> { return unsupported("startFleetRun"); }
  fleetRunEvents(..._args: Parameters<ApiClient["fleetRunEvents"]>): ReturnType<ApiClient["fleetRunEvents"]> { return unsupported("fleetRunEvents"); }
  cancelFleetRun(..._args: Parameters<ApiClient["cancelFleetRun"]>): ReturnType<ApiClient["cancelFleetRun"]> { return unsupported("cancelFleetRun"); }
  readmitFleetRun(..._args: Parameters<ApiClient["readmitFleetRun"]>): ReturnType<ApiClient["readmitFleetRun"]> { return unsupported("readmitFleetRun"); }
  rerunFleetRun(..._args: Parameters<ApiClient["rerunFleetRun"]>): ReturnType<ApiClient["rerunFleetRun"]> { return unsupported("rerunFleetRun"); }
  excludeRepository(..._args: Parameters<ApiClient["excludeRepository"]>): ReturnType<ApiClient["excludeRepository"]> { return unsupported("excludeRepository"); }
  includeRepository(..._args: Parameters<ApiClient["includeRepository"]>): ReturnType<ApiClient["includeRepository"]> { return unsupported("includeRepository"); }
  sharedPlans(..._args: Parameters<ApiClient["sharedPlans"]>): ReturnType<ApiClient["sharedPlans"]> { return unsupported("sharedPlans"); }
  planTemplates(..._args: Parameters<ApiClient["planTemplates"]>): ReturnType<ApiClient["planTemplates"]> { return unsupported("planTemplates"); }
  sharedPlan(..._args: Parameters<ApiClient["sharedPlan"]>): ReturnType<ApiClient["sharedPlan"]> { return unsupported("sharedPlan"); }
  inspectSharedPlanInputs(..._args: Parameters<ApiClient["inspectSharedPlanInputs"]>): ReturnType<ApiClient["inspectSharedPlanInputs"]> { return unsupported("inspectSharedPlanInputs"); }
  previewSharedPlanLock(..._args: Parameters<ApiClient["previewSharedPlanLock"]>): ReturnType<ApiClient["previewSharedPlanLock"]> { return unsupported("previewSharedPlanLock"); }
  createSharedPlanFromTemplate(..._args: Parameters<ApiClient["createSharedPlanFromTemplate"]>): ReturnType<ApiClient["createSharedPlanFromTemplate"]> { return unsupported("createSharedPlanFromTemplate"); }
  saveSharedPlan(..._args: Parameters<ApiClient["saveSharedPlan"]>): ReturnType<ApiClient["saveSharedPlan"]> { return unsupported("saveSharedPlan"); }
  previewSharedPlan(..._args: Parameters<ApiClient["previewSharedPlan"]>): ReturnType<ApiClient["previewSharedPlan"]> { return unsupported("previewSharedPlan"); }
  leagueLineups(..._args: Parameters<ApiClient["leagueLineups"]>): ReturnType<ApiClient["leagueLineups"]> { return unsupported("leagueLineups"); }
  inspectLeagueLineup(..._args: Parameters<ApiClient["inspectLeagueLineup"]>): ReturnType<ApiClient["inspectLeagueLineup"]> { return unsupported("inspectLeagueLineup"); }
  saveLeagueLineup(..._args: Parameters<ApiClient["saveLeagueLineup"]>): ReturnType<ApiClient["saveLeagueLineup"]> { return unsupported("saveLeagueLineup"); }
  startSharedPlanRun(..._args: Parameters<ApiClient["startSharedPlanRun"]>): ReturnType<ApiClient["startSharedPlanRun"]> { return unsupported("startSharedPlanRun"); }
  startTeamRun(..._args: Parameters<ApiClient["startTeamRun"]>): ReturnType<ApiClient["startTeamRun"]> { return unsupported("startTeamRun"); }
  recoveryStatus(): Promise<RecoveryStatus> { return unsupported("recoveryStatus"); }
  reconcileEffect(): Promise<WriteCommandResult<RecoveryStatus>> { return unsupported("reconcileEffect"); }
  acknowledgeEffect(): Promise<WriteCommandResult<RecoveryStatus>> { return unsupported("acknowledgeEffect"); }

  async league(options?: ReadRequestOptions): Promise<LeagueMap> {
    throwIfAborted(options?.signal);
    return {
      folders: [...this.locationsState],
      teams: cloneApiValue(this.leagueTeams),
      summary: { folders: this.locationsState.length, ...this.leagueSummary },
    };
  }

  async locations(options?: ReadRequestOptions): Promise<LocationsResponse> {
    throwIfAborted(options?.signal);
    return this.locationSnapshot();
  }

  async browse(path?: string, options?: ReadRequestOptions): Promise<BrowseResponse> {
    throwIfAborted(options?.signal);
    const current = path ?? this.browsePath;
    return { path: current, parent: "/workspace", directories: [`${current}/child`] };
  }

  async addLocation(path: string): Promise<WriteCommandResult<LocationsResponse>> {
    const next = this.locationsState.includes(path) ? this.locationsState : [...this.locationsState, path];
    return await this.commitLocations(next);
  }

  async removeLocation(path: string, expectedRevision: string): Promise<WriteCommandResult<LocationsResponse>> {
    if (expectedRevision !== this.locationSnapshot().revision) return this.stale("Configured locations changed since they were displayed");
    return await this.commitLocations(this.locationsState.filter((item) => item !== path));
  }

  private locationSnapshot(): LocationsResponse {
    const revision = stableTestRevision(this.locationsState);
    return { locations: [...this.locationsState], unavailable: [...this.unavailableLocationsState], revision };
  }

  private async commitLocations(next: string[]): Promise<WriteCommandResult<LocationsResponse>> {
    const failure = this.locationWriteFailures.shift();
    if (failure !== undefined) {
      return { outcome: "write-error", diagnosticId: `service-free:${failure}`, recovery: "blocked" };
    }
    if (this.mutations !== undefined) {
      const execution = await this.mutations.execute("locations", async () => {
        this.locationsState = [...next];
        return this.locationSnapshot();
      });
      return { outcome: "saved", value: execution.value, causalityRevision: execution.causalityRevision };
    }
    this.locationsState = [...next];
    return this.saved(this.locationSnapshot());
  }

  private stale<Value>(message: string): WriteCommandResult<Value> {
    return { outcome: "stale", diagnosticId: `service-free:${message}` };
  }

  private saved<Value>(value: Value): WriteCommandResult<Value> {
    this.causalityRevision += 1;
    return { outcome: "saved", value, causalityRevision: this.causalityRevision };
  }
}


function scoutWindow(hours: number) {
  const end = new Date("2026-08-22T12:00:00Z");
  const start = new Date(end.getTime() - hours * 60 * 60 * 1000);
  return { start_at: start.toISOString(), end_at: end.toISOString(), hours, run_assignment: "start_timestamp" as const };
}

function cloneApiValue<Value>(value: Value): Value {
  return structuredClone(value);
}


function committedSeedLocations(seed: InMemoryApiSeed): string[] {
  const locations = seed.locations;
  const leagueFolders = seed.league?.folders;
  if (locations !== undefined && leagueFolders !== undefined && !sameStrings(locations, leagueFolders)) {
    throw new Error("Service-free locations and League seeds must describe the same committed folders");
  }
  return [...(locations ?? leagueFolders ?? ["/workspace/base"])];
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function unsupported(operation: string): Promise<never> {
  return Promise.reject(new Error(`Service-free fixture does not seed ${operation}`));
}

class FleetReadGate implements DeferredFleetRead {
  readonly started: Promise<void>;
  readonly settled: Promise<"released" | "aborted">;
  private readonly released: Promise<void>;
  private resolveStarted!: () => void;
  private resolveReleased!: () => void;
  private resolveSettled!: (outcome: "released" | "aborted") => void;
  private settledOutcome: "released" | "aborted" | null = null;

  constructor() {
    this.started = new Promise((resolve) => { this.resolveStarted = resolve; });
    this.released = new Promise((resolve) => { this.resolveReleased = resolve; });
    this.settled = new Promise((resolve) => { this.resolveSettled = resolve; });
  }

  release(): void {
    this.resolveReleased();
  }

  async wait(signal?: AbortSignal): Promise<void> {
    this.resolveStarted();
    throwIfAborted(signal, () => this.settle("aborted"));
    await new Promise<void>((resolve, reject) => {
      const cleanup = () => signal?.removeEventListener("abort", abort);
      const abort = () => {
        cleanup();
        this.settle("aborted");
        reject(abortError());
      };
      signal?.addEventListener("abort", abort, { once: true });
      void this.released.then(() => {
        if (signal?.aborted) { abort(); return; }
        cleanup();
        this.settle("released");
        resolve();
      });
    });
  }

  private settle(outcome: "released" | "aborted"): void {
    if (this.settledOutcome !== null) return;
    this.settledOutcome = outcome;
    this.resolveSettled(outcome);
  }
}

function throwIfAborted(signal?: AbortSignal, onAbort?: () => void): void {
  if (!signal?.aborted) return;
  onAbort?.();
  throw abortError();
}

function abortError(): DOMException {
  return new DOMException("Read aborted", "AbortError");
}

function stableTestRevision(values: string[]): string {
  let hash = 2166136261;
  for (const value of values.join("\0")) { hash ^= value.charCodeAt(0); hash = Math.imul(hash, 16777619); }
  return Math.abs(hash >>> 0).toString(16).padStart(8, "0").repeat(8);
}
