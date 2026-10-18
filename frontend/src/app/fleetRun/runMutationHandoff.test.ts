import { describe, expect, it } from "vitest";

import { hasExpectedSavedRunIdentity, reviewChildRunId } from "@/app/fleetRun/runMutationHandoff";
import type { FleetRunState } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

const childRun = { fleet_run_id: "child-run" } as FleetRunState;
const saved = { outcome: "saved", value: childRun, causalityRevision: 4 } as const;

describe("reviewChildRunId", () => {
  it("follows the backend-created child while its parent still owns review selection", () => {
    expect(reviewChildRunId(saved, "parent-run", "parent-run")).toBe("child-run");
  });

  it("never overwrites newer review navigation that happened while the mutation was in flight", () => {
    expect(reviewChildRunId(saved, "parent-run", "other-run")).toBeNull();
    expect(reviewChildRunId(saved, "parent-run", null)).toBeNull();
  });

  it("never retargets review state for stale or failed writes", () => {
    expect(reviewChildRunId({ outcome: "stale", diagnosticId: "stale-1" }, "parent-run", "parent-run")).toBeNull();
    expect(reviewChildRunId({ outcome: "write-error", diagnosticId: "error-1", recovery: "refresh" }, "parent-run", "parent-run")).toBeNull();
  });

  it("requires ordinary saved mutation identity to match the issued Run identity", () => {
    const matching = { outcome: "saved", value: { fleet_run_id: "run-a" }, causalityRevision: 2 } as WriteCommandResult<FleetRunState>;
    const crossed = { outcome: "saved", value: { fleet_run_id: "run-b" }, causalityRevision: 2 } as WriteCommandResult<FleetRunState>;
    expect(hasExpectedSavedRunIdentity(matching, "run-a")).toBe(true);
    expect(hasExpectedSavedRunIdentity(crossed, "run-a")).toBe(false);
    expect(hasExpectedSavedRunIdentity({ outcome: "stale", diagnosticId: "stale" }, "run-a")).toBe(true);
  });
});
