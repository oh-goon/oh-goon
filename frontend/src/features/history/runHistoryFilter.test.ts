import { describe, expect, it } from "vitest";

import { FleetRunOutcome } from "@generated/enums";
import type { FleetRunHistoryItem } from "@generated/models";
import { runHistoryFilterOutcome, searchRunHistory } from "@/features/history/runHistoryFilter";

function item(id: string, outcome: FleetRunHistoryItem["outcome"], repository: string): FleetRunHistoryItem {
  return {
    outcome,
    run: {
      fleet_run_id: id,
      capability: { action: "FLEET_PLAN" },
      targets: [{ repository_id: repository, name: repository, path: `/work/${repository}` }],
    },
  } as unknown as FleetRunHistoryItem;
}

describe("runHistoryFilter", () => {
  const items = [
    item("run-good", FleetRunOutcome.SUCCESS, "api"),
    item("run-bad", FleetRunOutcome.FAILED, "frontend"),
    item("run-live", FleetRunOutcome.IN_PROGRESS, "worker"),
  ];

  it("projects filter chips onto backend-owned outcome query values", () => {
    expect(runHistoryFilterOutcome("all")).toBeNull();
    expect(runHistoryFilterOutcome("success")).toBe(FleetRunOutcome.SUCCESS);
    expect(runHistoryFilterOutcome("attention")).toBe(FleetRunOutcome.ATTENTION_REQUIRED);
    expect(runHistoryFilterOutcome("failed")).toBe(FleetRunOutcome.FAILED);
    expect(runHistoryFilterOutcome("active")).toBe(FleetRunOutcome.IN_PROGRESS);
  });

  it("searches loaded run and repository identity without changing scope", () => {
    expect(searchRunHistory(items, "front").map((entry) => entry.run.fleet_run_id)).toEqual(["run-bad"]);
    expect(searchRunHistory(items, "run-good").map((entry) => entry.run.fleet_run_id)).toEqual(["run-good"]);
  });

  it("finds a repository near the end of a large Shared Run without changing run scope", () => {
    const large = item("run-large", FleetRunOutcome.SUCCESS, "repo-00000");
    large.run.targets = Array.from({ length: 5000 }, (_, index) => ({
      repository_id: `repo-${String(index).padStart(5, "0")}`,
      name: `service-${String(index).padStart(5, "0")}`,
      path: `/work/service-${String(index).padStart(5, "0")}`,
    }));
    expect(searchRunHistory([large], "service-04999").map((entry) => entry.run.fleet_run_id)).toEqual(["run-large"]);
  });
});
