import {
  FleetRepositoryScope,
  OperationPhase,
  ReadinessState,
  ReasonCode,
  RiskLevel,
  Verdict,
} from "@generated/enums";
import type {
  DependencyRepositoryMatch,
  FleetRepository,
  FleetSnapshot,
  LeagueMap,
  RepositoryIdentity,
} from "@generated/models";

import { fixtureRepositoryInstanceId } from "./fixtures/identities";

export function createScaleRepositories(): RepositoryIdentity[] {
  return Array.from({ length: 5000 }, (_, index) => {
    const suffix = String(index).padStart(5, "0");
    return { repository_id: `repo-scale-${suffix}`, name: `service-${suffix}`, path: `/workspace/services/service-${suffix}` };
  });
}

export function createScaleLeague(repositories: readonly RepositoryIdentity[], sha256: string): LeagueMap {
  return {
    folders: ["/workspace/services"],
    summary: { folders: 1, git_repositories: repositories.length, configured_teams: 4875, missing_goon_yml: 100, invalid_goon_yml: 25 },
    teams: repositories.map((item, index) => ({
      repository: item,
      repository_instance_id: fixtureRepositoryInstanceId(item.repository_id),
      pipeline: index >= 4975
        ? { state: "INVALID", path: "goon.yml", reason: "ENTRYPOINT_UNREADABLE" }
        : index >= 4875
          ? { state: "MISSING", path: "goon.yml" }
          : { state: "CONFIGURED", path: "goon.yml", sha256, byte_count: 2048 },
    })),
  };
}

export function createIncludedFleet(repositories: readonly RepositoryIdentity[], generatedAt: string): FleetSnapshot {
  return createFleetSnapshot(repositories, generatedAt, false);
}

export function createScaleFleet(repositories: readonly RepositoryIdentity[], generatedAt: string): FleetSnapshot {
  return createFleetSnapshot(repositories, generatedAt, true);
}

function createFleetSnapshot(
  repositories: readonly RepositoryIdentity[],
  generatedAt: string,
  includeScaleVariation: boolean,
): FleetSnapshot {
  const fleetRepositories: FleetRepository[] = repositories.map((identity, index) => {
    const excluded = includeScaleVariation && index % 97 === 0;
    const attention = includeScaleVariation && !excluded && index % 31 === 0;
    if (excluded) {
      return {
        repository: identity,
        repository_instance_id: fixtureRepositoryInstanceId(identity.repository_id),
        scope: FleetRepositoryScope.EXCLUDED,
        readiness_state: ReadinessState.NOT_CHECKED,
        needs_attention: false,
        has_current_plan: false,
        current_branch_position: null,
        excluded_at: generatedAt,
        exclusion_reason: "Scale fixture exclusion",
        last_plan_verdict: Verdict.NOT_CHECKED,
        last_update_verdict: Verdict.NOT_CHECKED,
        last_verify_verdict: Verdict.NOT_CHECKED,
        init: null,
        plan: null,
        update: null,
        verify: null,
        guidance: {
          reason_code: ReasonCode.EXCLUDED,
          phase: OperationPhase.READY,
          risk: RiskLevel.NONE,
          can_auto_fix: false,
          auto_fix_action: null,
          evidence: {},
          system_actions: [],
        },
      };
    }
    return {
      repository: identity,
      repository_instance_id: fixtureRepositoryInstanceId(identity.repository_id),
      scope: FleetRepositoryScope.INCLUDED,
      readiness_state: attention ? ReadinessState.FAILED : ReadinessState.READY,
      needs_attention: attention,
      has_current_plan: true,
      current_branch_position: null,
      excluded_at: null,
      exclusion_reason: null,
      last_plan_verdict: Verdict.NOT_CHECKED,
      last_update_verdict: Verdict.NOT_CHECKED,
      last_verify_verdict: Verdict.NOT_CHECKED,
      guidance: {
        reason_code: attention ? ReasonCode.OPERATION_FAILED : ReasonCode.READY,
        phase: OperationPhase.READY,
        risk: attention ? RiskLevel.HIGH : RiskLevel.NONE,
        can_auto_fix: false,
        auto_fix_action: null,
        evidence: {},
        system_actions: [],
      },
    };
  });
  const excluded = fleetRepositories.filter((item) => item.scope === FleetRepositoryScope.EXCLUDED).length;
  const needsAttention = fleetRepositories.filter((item) => item.needs_attention).length;
  const revision = includeScaleVariation ? 5000 : 203;
  return {
    attention: { action_required: needsAttention, groups: [], healthy: fleetRepositories.length - needsAttention - excluded },
    provenance: {
      generated_at: generatedAt,
      journal_revision: revision,
      snapshot_id: includeScaleVariation ? "browser-scale-5000" : "browser-default",
      durable_revision: revision,
    },
    repositories: fleetRepositories,
    review_preparation: null,
    summary: { discovered: fleetRepositories.length, excluded, included: fleetRepositories.length - excluded, needs_attention: needsAttention, ready_for_update: fleetRepositories.length - excluded - needsAttention },
  };
}

export function scaleDependencyMatches(
  repositories: readonly RepositoryIdentity[],
  packageName: string,
  observedAt: string,
  fleetRunId: string,
): DependencyRepositoryMatch[] {
  if (packageName.toLowerCase() !== "mlflow") return [];
  return repositories.slice(0, 137).map((repository, index) => ({
    repository,
    ecosystem: "pypi" as const,
    package_name: "mlflow",
    resolved_versions: [index < 92 ? "2.17.2" : "2.18.0"],
    declared_ranges: [">=2"],
    relationship: "direct" as const,
    evidence_state: "current" as const,
    sources: ["uv.lock"],
    observed_at: observedAt,
    fleet_run_id: fleetRunId,
    operation_id: `operation-scale-dependency-${index + 1}`,
    producer_goon: "python/dependency-inventory",
    step_id: "dependency-inventory",
  }));
}
