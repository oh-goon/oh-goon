import { describe, expect, it } from "vitest";

import type { FleetRunHistoryPage } from "@generated/models";
import { projectHistoryAggregation, type HistoryAggregation } from "@/features/history/hooks/historyAggregation";

const empty: HistoryAggregation = { queryIdentity: "query", headRevision: null, items: [], cursor: null };

function page(next: number | null = null): FleetRunHistoryPage {
  return {
    journal_revision: 7,
    next_before_revision: next,
    items: [{ run: { fleet_run_id: "baseline" } }],
  } as unknown as FleetRunHistoryPage;
}

describe("projectHistoryAggregation", () => {
  it("publishes a newly accepted first page synchronously instead of exposing a false authoritative empty history", () => {
    const projected = projectHistoryAggregation(empty, "query", "revision-7", page());
    expect(projected.items[0]?.run.fleet_run_id).toBe("baseline");
    expect(projected.cursor).toBeNull();
  });

  it("keeps the owned aggregation after the synchronization effect catches up", () => {
    const owned: HistoryAggregation = { queryIdentity: "query", headRevision: "revision-7", items: page(5).items, cursor: 5 };
    expect(projectHistoryAggregation(owned, "query", "revision-7", page())).toBe(owned);
  });

  it("does not reuse stale aggregation while a new first page is still absent", () => {
    const stale: HistoryAggregation = { queryIdentity: "old", headRevision: "old-revision", items: page().items, cursor: null };
    expect(projectHistoryAggregation(stale, "new", null, null)).toEqual({ queryIdentity: "new", headRevision: null, items: [], cursor: null });
  });
});
