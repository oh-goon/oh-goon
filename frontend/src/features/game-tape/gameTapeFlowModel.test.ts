import { describe, expect, it } from "vitest";

import type { GameTapeRepositoryExecution, GameTapeStepExecution } from "@generated/models";
import { flowSourceIdentity, gameTapeFlowGroups, gameTapeFlowGroupStatusCounts, gameTapeFlowReviewFocus, gameTapeFlowSearchMatches, gameTapeFlowSegmentAnchor, gameTapeFlowSelectionContext, gameTapeFlowStatusCounts, gameTapeFlowStatusMatches } from "@/features/game-tape/gameTapeFlowModel";

function step(id: string, source: string | null, status = "PASSED", order = 1): GameTapeStepExecution {
  return {
    step_execution_id: `execution:${id}`,
    step_id: id,
    kind: "command",
    order,
    status,
    attempt_count: 1,
    retry_context: { classification: "NOT_RETRIED", retry_count: 0, linked_retry_count: 0, physical_context_changed: false },
    why: {
      declaration: source ? `${source}:10` : "resolved step",
      source_location: source ? { source, line: 10, column: 3 } : null,
      reached: true,
      explanation: "Reached in admitted order.",
    },
  } as GameTapeStepExecution;
}

function repository(steps: GameTapeStepExecution[]): GameTapeRepositoryExecution {
  return {
    repository_execution_id: "repository:1",
    repository: { repository_id: "repo", name: "repo", path: "/repo" },
    status: "PASSED",
    fingerprint: {},
    steps,
  } as unknown as GameTapeRepositoryExecution;
}

describe("Game Tape flow model", () => {
  it("recognizes recorded goon.yml provenance without inventing a Goon when source is absent", () => {
    expect(flowSourceIdentity("templates/goons/security/trivy-audit/goon.yml")).toEqual({ kind: "goon", displayName: "trivy-audit" });
    expect(flowSourceIdentity("goon.yml")).toEqual({ kind: "goon", displayName: null });
    expect(flowSourceIdentity("plans/python/production.yml")).toEqual({ kind: "source", displayName: "production.yml" });
    expect(flowSourceIdentity(null)).toEqual({ kind: "resolved", displayName: null });
  });

  it("groups only contiguous recorded sources so displayed arrows preserve admitted Step order", () => {
    const groups = gameTapeFlowGroups(repository([
      step("format", "templates/goons/python/python-quality/goon.yml", "PASSED", 1),
      step("lint", "templates/goons/python/python-quality/goon.yml", "PASSED", 2),
      step("audit", "templates/goons/security/trivy-audit/goon.yml", "PASSED", 3),
      step("report", "templates/goons/python/python-quality/goon.yml", "PASSED", 4),
    ]));
    expect(groups.map((group) => [group.displayName, group.steps.map((item) => item.step_id)])).toEqual([
      ["python-quality", ["format", "lint"]],
      ["trivy-audit", ["audit"]],
      ["python-quality", ["report"]],
    ]);
  });


  it("keeps group offsets and status summaries so renderers do not rescan lane steps", () => {
    const groups = gameTapeFlowGroups(repository([
      step("one", "templates/goons/python/python-quality/goon.yml", "PASSED", 1),
      step("two", "templates/goons/python/python-quality/goon.yml", "FAILED", 2),
      step("three", "templates/goons/security/trivy-audit/goon.yml", "PASSED", 3),
      step("four", null, "RUNNING", 4),
    ]));
    expect(groups.map((group) => group.laneStartIndex)).toEqual([0, 2, 3]);
    expect(groups[0]?.statusCounts).toEqual([{ status: "FAILED", count: 1 }, { status: "PASSED", count: 1 }]);
    expect(groups[1]?.statusCounts).toEqual([{ status: "PASSED", count: 1 }]);
    expect(groups[2]?.statusCounts).toEqual([{ status: "RUNNING", count: 1 }]);
  });

  it("summarizes each recorded Goon/source segment from its Step statuses only", () => {
    const [group] = gameTapeFlowGroups(repository([
      step("one", "templates/goons/python/python-quality/goon.yml", "PASSED", 1),
      step("two", "templates/goons/python/python-quality/goon.yml", "FAILED", 2),
    ]));
    expect(group).toBeDefined();
    if (group === undefined) return;
    expect(gameTapeFlowGroupStatusCounts(group)).toEqual([{ status: "FAILED", count: 1 }, { status: "PASSED", count: 1 }]);
  });

  it("summarizes recorded Step statuses without inventing a new execution outcome", () => {
    expect(gameTapeFlowStatusCounts([
      step("one", null, "PASSED", 1),
      step("two", null, "RUNNING", 2),
      step("three", null, "FAILED", 3),
      step("four", null, "PASSED", 4),
    ])).toEqual([
      { status: "FAILED", count: 1 },
      { status: "RUNNING", count: 1 },
      { status: "PASSED", count: 2 },
    ]);
  });

  it("chooses a lane-local review focus from recorded status without changing Step order", () => {
    const lane = repository([
      step("running", null, "RUNNING", 1),
      step("attention", null, "ATTENTION_REQUIRED", 2),
      step("failed", null, "FAILED", 3),
    ]);
    expect(gameTapeFlowReviewFocus(lane)?.step_id).toBe("failed");
    expect(gameTapeFlowReviewFocus(repository([step("passed", null, "PASSED", 1)]))).toBeNull();
  });
  it("finds repository, Goon/source and Step context without hiding or reordering matches", () => {
    const lane = repository([
      step("format", "templates/goons/python/python-quality/goon.yml", "PASSED", 1),
      step("audit", "templates/goons/security/trivy-audit/goon.yml", "PASSED", 2),
    ]);
    expect(gameTapeFlowSearchMatches([lane], "trivy-audit").map((match) => match.stepExecutionId)).toEqual(["execution:audit"]);
    expect(gameTapeFlowSearchMatches([lane], "repo").map((match) => match.stepExecutionId)).toEqual(["execution:format", "execution:audit"]);
    expect(gameTapeFlowSearchMatches([lane], "  ")).toEqual([]);
  });

  it("accepts only a canonical first Step as a durable source-segment anchor", () => {
    const lane = repository([
      step("format", "templates/goons/python/python-quality/goon.yml", "PASSED", 1),
      step("lint", "templates/goons/python/python-quality/goon.yml", "PASSED", 2),
      step("audit", "templates/goons/security/trivy-audit/goon.yml", "PASSED", 3),
    ]);
    expect(gameTapeFlowSegmentAnchor([lane], "execution:format")).toMatchObject({ repositoryExecutionId: "repository:1", firstStepExecutionId: "execution:format" });
    expect(gameTapeFlowSegmentAnchor([lane], "execution:lint")).toBeNull();
    expect(gameTapeFlowSegmentAnchor([lane], "execution:audit")).toMatchObject({ repositoryExecutionId: "repository:1", firstStepExecutionId: "execution:audit" });
    expect(gameTapeFlowSegmentAnchor([lane], "execution:stale")).toBeNull();
  });

  it("keeps previous/next selection navigation inside the selected repository lane", () => {
    const first = repository([step("one", "templates/goons/python/python-quality/goon.yml", "PASSED", 1), step("two", "templates/goons/security/trivy-audit/goon.yml", "PASSED", 2)]);
    const second = { ...repository([step("other", "templates/goons/general/report/goon.yml", "PASSED", 1)]), repository_execution_id: "repository:2", repository: { repository_id: "repo-2", name: "repo-2", path: "/repo-2" } } as GameTapeRepositoryExecution;
    const selected = gameTapeFlowSelectionContext([first, second], "execution:two");
    expect(selected?.repositoryIndex).toBe(0);
    expect(selected?.repositoryCount).toBe(2);
    expect(selected?.laneStepIndex).toBe(1);
    expect(selected?.laneStepCount).toBe(2);
    expect(selected?.previousStepExecutionId).toBe("execution:one");
    expect(selected?.nextStepExecutionId).toBeNull();
    expect(selected?.group.displayName).toBe("trivy-audit");
    expect(selected?.segmentIndex).toBe(1);
    expect(selected?.segmentCount).toBe(2);
    expect(selected?.previousSegmentFirstStepExecutionId).toBe("execution:one");
    expect(selected?.nextSegmentFirstStepExecutionId).toBeNull();
  });

  it("navigates exact recorded statuses in repository/Step order without hiding anything", () => {
    const first = repository([step("one", null, "FAILED", 1), step("two", null, "PASSED", 2)]);
    const second = { ...repository([step("three", null, "FAILED", 1)]), repository_execution_id: "repository:2", repository: { repository_id: "repo-2", name: "repo-2", path: "/repo-2" } } as GameTapeRepositoryExecution;
    expect(gameTapeFlowStatusMatches([first, second], "FAILED")).toEqual([
      { repositoryExecutionId: "repository:1", stepExecutionId: "execution:one" },
      { repositoryExecutionId: "repository:2", stepExecutionId: "execution:three" },
    ]);
  });

});
