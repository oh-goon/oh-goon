import type { MutationScope } from "@/domain/concurrency/MutationController";

/** Canonical data resources. Reads consume them; mutations invalidate them. */
export type ReadResource =
  | "runtime-health"
  | "runtime-progress"
  | "privacy"
  | "fleet-projection"
  | "scout-observations"
  | "execution-overview"
  | "run-failures"
  | "run-history"
  | "run-live-output"
  | "locations"
  | "league-map"
  | "shared-plans"
  | "plan-templates"
  | "league-lineups"
  | "filesystem"
  | "schedule-state"
  | "security-projection"
  | "executor-operations"
  | "executor-placement-queue"
  | "migration-projection"
  | "dependency-observations"
  | "dependency-selections"
  | "recovery-state"
;

const READ_RESOURCES = {
  health: ["runtime-health"],
  "runtime-discovery": ["runtime-progress"],
  privacy: ["privacy"],
  fleet: ["fleet-projection"],
  "scout-overview": ["scout-observations"],
  "scout-findings": ["scout-observations"],
  "scout-repository": ["scout-observations"],
  "scout-plan": ["scout-observations"],
  "scout-goon": ["scout-observations"],
  "scout-goon-leaders": ["scout-observations"],
  "scout-search": ["scout-observations"],
  "scout-metrics": ["scout-observations"],
  "scout-health": ["scout-observations"],
  "scout-run-evidence": ["scout-observations"],
  "execution-overview": ["execution-overview"],
  "execution-observations": ["execution-overview"],
  "failure-inventory": ["run-failures"],
  "failure-groups": ["run-failures"],
  "failure-repositories": ["run-failures"],
  "history-list": ["run-history"],
  "history-detail": ["run-history"],
  "agent-campaign": ["run-history"],
  "game-tape-diff": ["run-history"],
  "game-tape-performance": ["run-history"],
  "game-tape-artifacts": ["run-history"],
  "game-tape-failure-history": ["run-history"],
  "game-tape-live-output": ["run-live-output"],
  "history-list-more": ["run-history"],
  locations: ["locations"],
  "league-map": ["league-map", "locations"],
  "shared-plans": ["shared-plans", "run-history"],
  "shared-plan-document": ["shared-plans"],
  "shared-plan-lock-preview": ["shared-plans"],
  "shared-plan-input-inspection": ["shared-plans"],
  "shared-plan-preview": ["shared-plans", "league-map", "fleet-projection"],
  "plan-templates": ["plan-templates"],
  "league-lineups": ["league-lineups"],
  "league-lineup-inspection": ["league-lineups", "league-map"],
  "folder-browser": ["filesystem"],
  schedules: ["schedule-state"],
  "schedule-occurrence-repositories": ["schedule-state"],
  "fleet-commands": ["fleet-projection"],
  "executor-operations": ["executor-operations"],
  "executor-placement-queue": ["executor-placement-queue"],
  "fleet-security": ["security-projection", "fleet-projection"],
  "migration-preview": ["migration-projection", "fleet-projection"],
  "dependency-search": ["dependency-observations", "fleet-projection"],
  "dependency-overview": ["dependency-observations", "fleet-projection"],
  "dependency-advisory": ["dependency-observations", "fleet-projection"],
  "dependency-fleet-coverage": ["dependency-observations", "fleet-projection"],
  "dependency-repositories": ["dependency-observations", "fleet-projection"],
  "dependency-coverage-repositories": ["dependency-observations", "fleet-projection"],
  "repository-dependencies": ["dependency-observations", "fleet-projection"],
  "dependency-selection": ["dependency-selections"],
  "fleet-run-events": ["run-history"],
  "fleet-run-detail": ["run-history"],
  recovery: ["recovery-state", "run-history", "schedule-state", "executor-operations"],
} as const satisfies Readonly<Record<string, readonly ReadResource[]>>;

export type ReadScope = keyof typeof READ_RESOURCES;

const MUTATION_RESOURCES: Readonly<Record<MutationScope, readonly ReadResource[]>> = {
  "fleet-run": ["runtime-progress", "fleet-projection", "execution-overview", "run-failures", "run-history", "schedule-state", "security-projection", "executor-operations", "executor-placement-queue", "scout-observations"],
  locations: ["locations", "league-map", "league-lineups", "fleet-projection", "security-projection", "migration-projection", "dependency-observations"],
  repositories: ["fleet-projection", "security-projection", "migration-projection", "dependency-observations"],
  schedules: ["schedule-state"],
  recovery: ["recovery-state", "run-history"],
  plans: ["shared-plans", "plan-templates"],
  "league-lineups": ["league-lineups"],
  "dependency-selections": ["dependency-selections"],
};

const GLOBAL_DURABLE_MUTATION_RESOURCES = ["runtime-health"] as const satisfies readonly ReadResource[];
const AUTHORING_ONLY_MUTATION_SCOPES = new Set<MutationScope>(["plans", "league-lineups", "dependency-selections"]);
const FLEET_PROGRESS_RESOURCES = [
  "fleet-projection",
  "execution-overview",
  "run-history",
  "run-failures",
  "schedule-state",
  "security-projection",
  "migration-projection",
  "dependency-observations",
  "scout-observations",
  "executor-operations",
  "executor-placement-queue",
] as const satisfies readonly ReadResource[];

export const READ_SCOPES = Object.freeze(Object.keys(READ_RESOURCES) as ReadScope[]);

export function resourcesInvalidatedByMutation(scope: MutationScope): readonly ReadResource[] {
  const durableResources = AUTHORING_ONLY_MUTATION_SCOPES.has(scope) ? [] : GLOBAL_DURABLE_MUTATION_RESOURCES;
  return [...new Set<ReadResource>([...durableResources, ...MUTATION_RESOURCES[scope]])];
}

export function readScopesInvalidatedByMutation(scope: MutationScope): readonly ReadScope[] {
  return readScopesForResources(resourcesInvalidatedByMutation(scope));
}

export function readScopesForResources(resources: readonly ReadResource[]): readonly ReadScope[] {
  const invalidated = new Set(resources);
  return (Object.keys(READ_RESOURCES) as ReadScope[]).filter((scope) =>
    READ_RESOURCES[scope].some((resource) => invalidated.has(resource)),
  );
}


/** Reads whose derived data can change as an already-admitted FleetRun commits results. */
export function readScopesInvalidatedByFleetProgress(): readonly ReadScope[] {
  return readScopesForResources(FLEET_PROGRESS_RESOURCES);
}
