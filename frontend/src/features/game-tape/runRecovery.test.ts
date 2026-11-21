import { describe, expect, it } from "vitest";

import { FleetRunStatus, OperationAction } from "@generated/enums";
import type { FailedFleetRun, OperationAction as OperationActionModel } from "@generated/models";
import { gameTapeRecoveryAction, sameRunRepositoryIds } from "@/features/game-tape/runRecovery";
import { fixtureRunFeedback } from "../../../e2e/fixtures/runFeedback";

function failedRun(code: string, action: OperationActionModel = OperationAction.EXECUTE_PLAN): FailedFleetRun {
  return {
    status: FleetRunStatus.FAILED,
    fleet_run_id: "run-1",
    target: null,
    capability: {
      action,
      capability_id: `fixture:${action}`,
      policy_fingerprint: "fixture-policy",
      safety: "OPAQUE_MUTATION",
      target_kind: "NONE",
    },
    repository_scope: { kind: "EXPLICIT", repository_ids: ["repo-a", "repo-b"] },
    targets: [
      { repository_id: "repo-a", name: "A", path: "/a" },
      { repository_id: "repo-b", name: "B", path: "/b" },
    ],
    accepted_at: "2026-08-21T06:00:00Z",
    started_at: "2026-08-21T06:00:01Z",
    finished_at: "2026-08-21T06:00:02Z",
    progress: { total: 2, succeeded: 0, failed: 0, needs_attention: 0, completed: 0 },
    failure: { code, kind: "CONFLICT", retryable: false, stage: "EXECUTION", message: code },
  };
}

describe("gameTapeRecoveryAction", () => {
  it.each(["readmit-placement", "open-recovery", "run-same-target", "none"] as const)("presents the backend action %s for the exact Run cut", (action) => {
    expect(gameTapeRecoveryAction(fixtureRunFeedback("run-1", { recovery_action: action }), "run-1", 203)).toBe(action);
  });

  it("does not authorize controls from absent, foreign, omitted or stale feedback", () => {
    expect(gameTapeRecoveryAction(undefined, "run-1")).toBe("none");
    expect(gameTapeRecoveryAction(fixtureRunFeedback("other", { recovery_action: "run-same-target" }), "run-1")).toBe("none");
    expect(gameTapeRecoveryAction(fixtureRunFeedback("run-1", { recovery_action: "run-same-target", fleet_run_id: null }), "run-1")).toBe("none");
    expect(gameTapeRecoveryAction(fixtureRunFeedback("run-1", { recovery_action: "run-same-target" }), "run-1", 204)).toBe("none");
    expect(gameTapeRecoveryAction(fixtureRunFeedback("run-1", { recovery_action: "run-same-target" }), "run-1", 203, "RUNNING")).toBe("none");
    expect(gameTapeRecoveryAction(fixtureRunFeedback("run-1", { recovery_action: "run-same-target" }), "run-1", 203, "FAILED", "SUCCESS")).toBe("none");
  });

  it("preserves the original recorded repository targets", () => {
    expect(sameRunRepositoryIds(failedRun("COMMAND_FAILED"))).toEqual(["repo-a", "repo-b"]);
  });
});
