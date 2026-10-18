import { describe, expect, it } from "vitest";

import { EMPTY_RUN_REVIEW_STATE, patchRunReviewState, runReviewUrl } from "@/app/pages/runReviewState";

describe("runReviewUrl", () => {
  it("builds a stable browser deep link from canonical Run and Step identities", () => {
    const state = patchRunReviewState(EMPTY_RUN_REVIEW_STATE, { runId: "fleet run/1", stepExecutionId: "repo:api:step:2", view: "timeline" });
    const baseHref = ["http", "://127.0.0.1/workspace"].join("");
    const origin = ["http", "://127.0.0.1"].join("");
    expect(runReviewUrl(state, baseHref)).toBe(`${origin}/#runs?run=fleet+run%2F1&step=repo%3Aapi%3Astep%3A2&view=timeline`);
  });

  it("serializes every durable review concern without a positional helper chain", () => {
    const state = patchRunReviewState(EMPTY_RUN_REVIEW_STATE, {
      runId: "current", stepExecutionId: "step-2", view: "logs", baselineRunId: "baseline-1",
      failureFocus: true, logQuery: "Traceback warning", logStream: "stderr", rawLogsOpen: true,
      technicalOpen: true, flowQuery: "trivy audit", collapsedLaneIds: ["repo:api", "repo:web"],
      sourceSegmentStepExecutionId: "repo:api:step:1",
    });
    const baseHref = ["http", "://127.0.0.1/workspace"].join("");
    const origin = ["http", "://127.0.0.1"].join("");
    expect(runReviewUrl(state, baseHref)).toBe(`${origin}/#runs?run=current&step=step-2&view=logs&baseline=baseline-1&focus=failures&log_q=Traceback+warning&log_stream=stderr&raw=1&tech=1&flow_q=trivy+audit&fold=repo%3Aapi&fold=repo%3Aweb&segment=repo%3Aapi%3Astep%3A1`);
  });
});
