import type { DependencyPackageOverview, DependencyRepositoryMatch, RepositoryIdentity } from "@generated/models";

import { DEFAULT_RUN_ID } from "./runIds";
import { FIXTURE_REPOSITORIES } from "./identities";
import { FIXTURE_NOW as NOW } from "./time";

export function dependencyMatches(packageName: string): DependencyRepositoryMatch[] {
  if (packageName.toLowerCase() !== "mlflow") return [];
  const rows = [
    [FIXTURE_REPOSITORIES.payments, ["2.17.2"], "current", NOW],
    [FIXTURE_REPOSITORIES.web, ["2.17.1"], "current", NOW],
    [FIXTURE_REPOSITORIES.worker, ["2.18.0"], "current", NOW],
    [FIXTURE_REPOSITORIES.search, ["2.18.0rc1"], "current", NOW],
    [FIXTURE_REPOSITORIES.catalog, ["2.16.0"], "unknown", "2026-08-10T12:00:00Z"],
  ] as const;
  return rows.map(([repository, versions, evidenceState, observedAt], index) => ({
    repository,
    ecosystem: "pypi" as const,
    package_name: "mlflow",
    resolved_versions: [...versions],
    contexts: ["lock"],
    evidence: { scope: "locked-inventory", freshness: evidenceState, completeness: "complete", availability: "available", absence_admissible: evidenceState === "current" },
    sources: ["uv.lock"],
    observed_at: observedAt,
    fleet_run_id: DEFAULT_RUN_ID,
    operation_id: `operation-dependency-${index + 1}`,
    producer_goon: "python/dependency-inventory",
    step_id: "dependency-inventory",
  }));
}

export function emptyDependencyOverview(
  packageName: string,
  repositories: readonly RepositoryIdentity[],
): DependencyPackageOverview {
  return {
    ecosystem: "pypi",
    package_name: packageName,
    observed_repositories: 0,
    latest_observed_at: null,
    freshness: { current: 0, no_matching_dependency: 0, unknown: 0, stale: 0, no_current_evidence: repositories.length },
    versions: [],
  };
}
