import type { DependencyCoverage, DependencyEvidence } from "@/features/dependencies/DependenciesFeature";

export interface DependencyReviewState {
  q: string;
  packageName: string;
  resolvedVersion: string;
  versionSpecifier: string;
  advisoryId: string;
  evidence: DependencyEvidence;
  coverage: DependencyCoverage;
  offset: number;
  repositoryId: string;
}

const EVIDENCE = new Set<DependencyEvidence>(["all", "current", "unknown", "stale"]);
const COVERAGE = new Set<DependencyCoverage>(["matching", "absent_from_lock", "no_current_evidence"]);

export function dependencyReviewStateFromHash(hash: string): DependencyReviewState {
  const query = hash.split("?", 2)[1] ?? "";
  const params = new URLSearchParams(query);
  const evidence = params.get("evidence") ?? "all";
  const coverage = params.get("coverage") ?? "matching";
  const offset = Number.parseInt(params.get("offset") ?? "0", 10);
  const normalizedCoverage = COVERAGE.has(coverage as DependencyCoverage) ? coverage as DependencyCoverage : "matching";
  const matching = normalizedCoverage === "matching";
  return {
    q: params.get("q")?.trim() ?? "",
    packageName: params.get("package")?.trim() ?? "",
    resolvedVersion: matching ? params.get("version")?.trim() ?? "" : "",
    versionSpecifier: matching ? params.get("affected")?.trim() ?? "" : "",
    advisoryId: matching ? params.get("advisory")?.trim() ?? "" : "",
    evidence: matching && EVIDENCE.has(evidence as DependencyEvidence) ? evidence as DependencyEvidence : "all",
    coverage: normalizedCoverage,
    offset: Number.isFinite(offset) && offset >= 0 ? offset : 0,
    repositoryId: params.get("repository")?.trim() ?? "",
  };
}

export function dependencyQuery(state: Partial<DependencyReviewState>): Record<string, string> {
  const result: Record<string, string> = {};
  if (state.q) result.q = state.q;
  if (state.packageName) result.package = state.packageName;
  if (state.resolvedVersion) result.version = state.resolvedVersion;
  if (state.versionSpecifier) result.affected = state.versionSpecifier;
  if (state.advisoryId) result.advisory = state.advisoryId;
  if (state.evidence && state.evidence !== "all") result.evidence = state.evidence;
  if (state.coverage && state.coverage !== "matching") result.coverage = state.coverage;
  if (state.offset && state.offset > 0) result.offset = String(state.offset);
  if (state.repositoryId) result.repository = state.repositoryId;
  return result;
}
