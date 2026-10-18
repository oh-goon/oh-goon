import { describe, expect, it } from "vitest";

import { EMPTY_RUN_REVIEW_STATE, normalizeRunReviewStateForLoadedRun, parseRunReviewState, patchRunReviewState, runReviewHash, writeRunReviewState } from "@/app/pages/runReviewState";

describe("RunReviewState", () => {
  it("round-trips every durable review field through one typed owner", () => {
    const state = {
      ...EMPTY_RUN_REVIEW_STATE,
      runId: "run 1",
      stepExecutionId: "repo:api:step:2",
      view: "logs" as const,
      baselineRunId: "baseline-1",
      historyQuery: "failed api",
      historyFilter: "failed" as const,
      failureFocus: true,
      logQuery: "Traceback warning",
      logStream: "stderr" as const,
      logPageFromEnd: 7,
      rawLogsOpen: true,
      technicalOpen: true,
      flowQuery: "trivy audit",
      collapsedLaneIds: ["repo:api", "repo:web"],
      sourceSegmentStepExecutionId: "repo:api:step:1",
    };
    const hash = runReviewHash(state);
    expect(parseRunReviewState(hash)).toEqual(state);
  });

  it("validates enum-like presentation values instead of accepting arbitrary URL semantics", () => {
    const state = parseRunReviewState("#runs?view=magic&history_filter=maybe&log_stream=combined&log_page=-4");
    expect(state.view).toBeNull();
    expect(state.historyFilter).toBe("all");
    expect(state.logStream).toBe("all");
    expect(state.logPageFromEnd).toBe(0);
  });

  it("patches one review concern without dropping unrelated state", () => {
    const state = parseRunReviewState("#runs?run=run-1&view=flow&step=step-1&focus=failures&fold=repo%3Aapi&flow_q=trivy");
    const next = patchRunReviewState(state, { stepExecutionId: "step-2" });
    expect(runReviewHash(next)).toBe("#runs?run=run-1&step=step-2&view=flow&focus=failures&flow_q=trivy&fold=repo%3Aapi");
  });
  it("retargets a Run by preserving history context and clearing all Run-local review state", () => {
    const state = parseRunReviewState("#runs?run=old&step=s1&view=logs&baseline=b1&history_q=failed&history_filter=failed&focus=failures&log_q=Traceback&log_stream=stderr&log_page=4&raw=1&tech=1&flow_q=trivy&fold=lane-1&segment=seg-1");
    const next = patchRunReviewState(state, { runId: "new" });
    expect(next).toMatchObject({
      runId: "new",
      historyQuery: "failed",
      historyFilter: "failed",
      stepExecutionId: null,
      view: null,
      baselineRunId: null,
      failureFocus: false,
      logQuery: "",
      logStream: "all",
      logPageFromEnd: 0,
      rawLogsOpen: false,
      technicalOpen: false,
      flowQuery: "",
      collapsedLaneIds: [],
      sourceSegmentStepExecutionId: null,
    });
  });

  it("applies only explicit new-Run review fields after clearing the previous Run context", () => {
    const state = parseRunReviewState("#runs?run=old&step=s1&view=logs&focus=failures&log_q=Traceback&raw=1&flow_q=trivy&history_q=keep");
    const next = patchRunReviewState(state, { runId: "new", view: "flow" });
    expect(next).toMatchObject({
      runId: "new",
      view: "flow",
      historyQuery: "keep",
      stepExecutionId: null,
      failureFocus: false,
      logQuery: "",
      rawLogsOpen: false,
      flowQuery: "",
    });
  });

  it("normalizes stale Run-local review identity against loaded canonical facts", () => {
    const state = parseRunReviewState("#runs?run=r1&step=stale&view=diff&baseline=missing&focus=failures&log_page=3&tech=1&fold=lane-ok&fold=lane-stale&fold=lane-ok&segment=segment-stale&history_q=keep-me");
    const next = normalizeRunReviewStateForLoadedRun(state, {
      runId: "r1",
      stepExecutionIds: new Set(["step-ok"]),
      baselineRunIds: new Set(),
      repositoryExecutionIds: new Set(["lane-ok"]),
      sourceSegmentStepExecutionIds: new Set(["segment-ok"]),
      hasIssues: false,
    });
    expect(next).toMatchObject({ stepExecutionId: null, baselineRunId: null, view: "summary", failureFocus: false, logPageFromEnd: 0, technicalOpen: false, collapsedLaneIds: ["lane-ok"], sourceSegmentStepExecutionId: null, historyQuery: "keep-me" });
  });

  it("does not normalize against canonical facts from a different loaded Run", () => {
    const state = parseRunReviewState("#runs?run=requested&step=step-stale&view=diff&baseline=b-stale&fold=lane-stale&segment=segment-stale");
    const next = normalizeRunReviewStateForLoadedRun(state, {
      runId: "still-loading-previous-run",
      stepExecutionIds: new Set(),
      baselineRunIds: new Set(),
      repositoryExecutionIds: new Set(),
      sourceSegmentStepExecutionIds: new Set(),
      hasIssues: false,
    });
    expect(next).toEqual(state);
  });

  it("preserves requested Diff identity until comparison-history authority is loaded", () => {
    const state = parseRunReviewState("#runs?run=r1&view=diff&baseline=b1");
    const next = normalizeRunReviewStateForLoadedRun(state, {
      runId: "r1",
      stepExecutionIds: new Set(),
      baselineRunIds: null,
      repositoryExecutionIds: new Set(),
      sourceSegmentStepExecutionIds: new Set(),
      hasIssues: false,
    });
    expect(next.view).toBe("diff");
    expect(next.baselineRunId).toBe("b1");
  });

  it("keeps Diff available without inventing a baseline when comparison options exist", () => {
    const state = parseRunReviewState("#runs?run=r1&view=diff&baseline=missing");
    const next = normalizeRunReviewStateForLoadedRun(state, {
      runId: "r1",
      stepExecutionIds: new Set(),
      baselineRunIds: new Set(["eligible"]),
      repositoryExecutionIds: new Set(),
      sourceSegmentStepExecutionIds: new Set(),
      hasIssues: false,
    });
    expect(next.view).toBe("diff");
    expect(next.baselineRunId).toBeNull();
  });

  it("preserves valid canonical Run-local state", () => {
    const state = parseRunReviewState("#runs?run=r1&step=step-ok&view=diff&baseline=b1&focus=failures&log_page=2&tech=1&fold=lane-ok&segment=segment-ok");
    expect(normalizeRunReviewStateForLoadedRun(state, {
      runId: "r1",
      stepExecutionIds: new Set(["step-ok"]),
      baselineRunIds: new Set(["b1"]),
      repositoryExecutionIds: new Set(["lane-ok"]),
      sourceSegmentStepExecutionIds: new Set(["segment-ok"]),
      hasIssues: true,
    })).toEqual(state);
  });


  it("round-trips the complete history-first filter context independently of Run review state", () => {
    const state = {
      ...EMPTY_RUN_REVIEW_STATE,
      historyQuery: "failed service",
      historyFilter: "failed" as const,
      historyOutcome: "FAILED" as const,
      historyStatus: "FAILED" as const,
      historySource: "SCHEDULED" as const,
      historyRepositoryId: "repo-scale-00092",
      historyFromAt: "2026-08-28T00:00:00Z",
      historyToAt: "2026-08-29T00:00:00Z",
      historyPlanQuery: "Python production quality",
    };
    const hash = runReviewHash(state);
    expect(parseRunReviewState(hash)).toEqual(state);
    expect(hash).not.toContain("run=");
    expect(hash).toContain("repository=repo-scale-00092");
    expect(hash).toContain("history_outcome=FAILED");
  });

  it("returns to history without carrying any hidden Run-review context", () => {
    const state = parseRunReviewState("#runs?run=r1&step=s1&view=timeline&baseline=b1&repository=repo-scale-00092&history_from=2026-08-28T00%3A00%3A00Z&history_to=2026-08-29T00%3A00%3A00Z&history_plan=Python+production+quality&history_outcome=FAILED&focus=failures&log_q=warning&log_stream=stderr&raw=1&tech=1&flow_q=trivy&fold=lane-1&segment=seg-1");
    const next = patchRunReviewState(state, { runId: null });
    expect(next).toMatchObject({
      runId: null,
      historyRepositoryId: "repo-scale-00092",
      historyOutcome: "FAILED",
      historyPlanQuery: "Python production quality",
      stepExecutionId: null,
      view: null,
      baselineRunId: null,
      failureFocus: false,
      logQuery: "",
      logStream: "all",
      rawLogsOpen: false,
      technicalOpen: false,
      flowQuery: "",
      collapsedLaneIds: [],
      sourceSegmentStepExecutionId: null,
    });
    expect(runReviewHash(next)).toBe("#runs?history_outcome=FAILED&repository=repo-scale-00092&history_from=2026-08-28T00%3A00%3A00Z&history_to=2026-08-29T00%3A00%3A00Z&history_plan=Python+production+quality");
  });

  it("canonicalizes malformed history-only URLs so review state cannot exist without a Run", () => {
    const state = parseRunReviewState("#runs?view=logs&focus=failures&log_q=warning&log_stream=stderr&raw=1&tech=1&flow_q=trivy&fold=lane-1&segment=seg-1&history_q=keep");
    expect(state).toEqual({ ...EMPTY_RUN_REVIEW_STATE, historyQuery: "keep" });
    expect(runReviewHash(state)).toBe("#runs?history_q=keep");
  });

  it("never carries explicit failure focus across Run identity", () => {
    const state = parseRunReviewState("#runs?run=failed-1&focus=failures");
    expect(patchRunReviewState(state, { runId: "failed-2" }).failureFocus).toBe(false);
  });

  it("never invents failure focus when retargeting a fresh Run", () => {
    const state = parseRunReviewState("#runs?run=passed&view=flow");
    expect(patchRunReviewState(state, { runId: "failed" }).failureFocus).toBe(false);
  });

  it("publishes same-page review writes through the one browser-state owner", () => {
    window.history.replaceState({}, "", "/#runs");
    let notifications = 0;
    const onLocation = () => { notifications += 1; };
    window.addEventListener("popstate", onLocation);
    try {
      writeRunReviewState("replace", { ...EMPTY_RUN_REVIEW_STATE, runId: "run-1", view: "flow" });
      expect(window.location.hash).toBe("#runs?run=run-1&view=flow");
      expect(notifications).toBe(1);
    } finally {
      window.removeEventListener("popstate", onLocation);
    }
  });

});
