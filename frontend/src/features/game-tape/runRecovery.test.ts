import { describe, expect, it } from "vitest";

import { FleetRunStatus, OperationAction } from "@generated/enums";
import type { FailedFleetRun, OperationAction as OperationActionModel } from "@generated/models";
import { gameTapeRecoveryAction, sameRunRepositoryIds } from "@/features/game-tape/runRecovery";

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
  it("uses durable readmission only for executor placement loss", () => {
    expect(gameTapeRecoveryAction(failedRun("EXECUTOR_PLACEMENT_LOST"), true)).toBe("readmit-placement");
  });

  it("routes uncertain repository effects to evidence-first Recovery", () => {
    expect(gameTapeRecoveryAction(failedRun("REPOSITORY_EFFECT_OUTCOME_UNKNOWN"), true)).toBe("open-recovery");
    expect(gameTapeRecoveryAction(failedRun("RUN_PARTIALLY_COMMITTED_BY_RESTART"), true)).toBe("open-recovery");
  });

  it("reruns the exact recorded target for ordinary execution failures", () => {
    const run = failedRun("COMMAND_FAILED");
    expect(gameTapeRecoveryAction(run, true)).toBe("run-same-target");
    expect(sameRunRepositoryIds(run)).toEqual(["repo-a", "repo-b"]);
  });

  it("uses projected rerun policy rather than action identity", () => {
    expect(
      gameTapeRecoveryAction(failedRun("COMMAND_FAILED", OperationAction.PREPARE_REVIEW), true),
    ).toBe("run-same-target");
    expect(
      gameTapeRecoveryAction(failedRun("COMMAND_FAILED", OperationAction.EXECUTE_PLAN), false),
    ).toBe("none");
  });
});
