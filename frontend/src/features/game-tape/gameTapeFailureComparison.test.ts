import { describe, expect, it } from "vitest";

import type { GameTapeFailureHistory } from "@generated/models";
import { gameTapePreviousExactComparableRunId } from "@/features/game-tape/gameTapeFailureComparison";

function history(runIds: readonly string[]): GameTapeFailureHistory {
  return {
    occurrences: runIds.map((fleet_run_id, index) => ({ fleet_run_id, accepted_at: `2026-08-${21 - index}T10:00:00Z` })),
  } as unknown as GameTapeFailureHistory;
}

describe("gameTapePreviousExactComparableRunId", () => {
  it("uses only the nearest backend-recorded prior exact occurrence when it is a loaded comparison option", () => {
    expect(gameTapePreviousExactComparableRunId(history(["run-nearest", "run-older"]), [{ runId: "run-nearest" }, { runId: "run-older" }])).toBe("run-nearest");
  });

  it("does not fall through to an older occurrence when the nearest exact occurrence is not comparison-eligible", () => {
    expect(gameTapePreviousExactComparableRunId(history(["run-nearest", "run-older"]), [{ runId: "run-older" }])).toBeNull();
  });

  it("returns null without canonical prior occurrence evidence", () => {
    expect(gameTapePreviousExactComparableRunId(history([]), [{ runId: "run-older" }])).toBeNull();
  });
});
