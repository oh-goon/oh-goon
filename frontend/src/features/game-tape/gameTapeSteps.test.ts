import type { GameTapeStepExecution } from "@generated/models";
import { gameTapeFailureContexts, gameTapeFailureNavigation, type GameTapeStepContext } from "@/features/game-tape/gameTapeSteps";

function context(id: string, status: string, failure = false): GameTapeStepContext {
  return {
    repository: { repository_execution_id: "repo:execution", repository: { repository_id: "repo", name: "repo", path: "/repo" }, status: "FAILED", steps: [] } as unknown as GameTapeStepContext["repository"],
    step: { step_execution_id: id, step_id: id, kind: "command", order: 1, status, retry_context: {}, why: {}, failure: failure ? { exit_code: 1, signature_id: "sig", raw_log_available: false } : null } as unknown as GameTapeStepExecution,
  };
}

describe("Game Tape failure navigation", () => {
  it("keeps only canonical failed/interrupt/fingerprint Steps in admitted order", () => {
    const contexts = [context("passed", "PASSED"), context("failed", "FAILED"), context("attention", "ATTENTION_REQUIRED"), context("fingerprint", "PASSED", true), context("interrupted", "INTERRUPTED")];
    expect(gameTapeFailureContexts(contexts).map(({ step }) => step.step_execution_id)).toEqual(["failed", "fingerprint", "interrupted"]);
    expect(gameTapeFailureNavigation(contexts, "failed").next?.step.step_execution_id).toBe("fingerprint");
    expect(gameTapeFailureNavigation(contexts, "interrupted").previous?.step.step_execution_id).toBe("fingerprint");
    expect(gameTapeFailureNavigation(contexts, "passed").next?.step.step_execution_id).toBe("failed");
  });
});
