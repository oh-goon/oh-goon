import { describe, expect, it } from "vitest";

import { FleetRepositoryScope, OperationPhase, ReadinessState, ReasonCode, RiskLevel, Verdict } from "@generated/enums";
import type { IncludedFleetRepository, ReviewPreparation } from "@generated/models";
import { maintenanceState } from "@/features/maintenance/model";

function repo(overrides: Partial<IncludedFleetRepository> = {}): IncludedFleetRepository {
  return {
    repository: { repository_id: "repo-1", name: "repo", path: "/repo" },
    repository_instance_id: "fixture-instance:repo-1",
    scope: FleetRepositoryScope.INCLUDED,
    readiness_state: ReadinessState.READY,
    needs_attention: false,
    has_current_plan: false,
    current_branch_position: null,
    excluded_at: null,
    exclusion_reason: null,
    last_plan_verdict: Verdict.NOT_CHECKED,
    last_update_verdict: Verdict.NOT_CHECKED,
    last_verify_verdict: Verdict.NOT_CHECKED,
    guidance: {
      reason_code: ReasonCode.READY,
      phase: OperationPhase.READY,
      risk: RiskLevel.NONE,
      can_auto_fix: false,
      auto_fix_action: null,
      evidence: {},
      system_actions: [],
    },
    ...overrides,
  };
}

describe("maintenanceState", () => {
  it("starts with repository setup when there is no included fleet", () => {
    expect(maintenanceState([], [], null, null).stage).toBe("add-repositories");
  });

  it("previews after the selected repository is ready", () => {
    const repository = repo();
    expect(maintenanceState([repository], [repository.repository.repository_id], null, null).stage).toBe("preview");
  });

  it("applies after the repository has a canonical current plan", () => {
    const repository = repo({ has_current_plan: true });
    expect(maintenanceState([repository], [repository.repository.repository_id], null, null).stage).toBe("apply");
  });

  it("prioritizes review preparation after update invalidates readiness", () => {
    const repository = repo({ readiness_state: ReadinessState.STALE });
    const review = { repositories: [repository.repository], update_fleet_run_id: "run-1", target: null } as ReviewPreparation;
    expect(maintenanceState([repository], [repository.repository.repository_id], null, review).stage).toBe("review");
  });

  it("directs failed repositories to issue resolution", () => {
    const repository = repo({ readiness_state: ReadinessState.FAILED, needs_attention: true });
    expect(maintenanceState([repository], [repository.repository.repository_id], null, null).stage).toBe("resolve");
  });

  it("resolves real failures before preparing successful siblings for review", () => {
    const failed = repo({ repository: { repository_id: "repo-failed", name: "failed", path: "/failed" }, readiness_state: ReadinessState.FAILED, needs_attention: true });
    const updated = repo({ repository: { repository_id: "repo-updated", name: "updated", path: "/updated" }, readiness_state: ReadinessState.STALE });
    const review = { repositories: [updated.repository], update_fleet_run_id: "run-1", target: null } as ReviewPreparation;
    expect(maintenanceState([failed, updated], ["repo-failed", "repo-updated"], null, review).stage).toBe("resolve");
  });
  it("uses canonical repository attention for current operation failures", () => {
    const repository = repo({ needs_attention: true });
    expect(maintenanceState([repository], [repository.repository.repository_id], null, null).stage).toBe("resolve");
  });

});
