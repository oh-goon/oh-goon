import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { FleetRunHistoryItem } from "@generated/models";
import { FleetRunOutcome } from "@generated/enums";
import { RunHistory } from "@/features/history/components/RunHistory";

describe("RunHistory reachability", () => {
  it("keeps older pages reachable when the loaded page has no filter matches", () => {
    const onLoadMore = vi.fn();
    const loaded: FleetRunHistoryItem[] = [{
      admitted_revision: 1,
      execution: { kind: "direct" },
      outcome: FleetRunOutcome.SUCCESS,
      run: {
        fleet_run_id: "run-success",
        capability: {
          capability_id: "fleet-plan",
          policy_fingerprint: "policy-1",
          action: "execute-plan",
          target_kind: "FLEET_PLAN",
          safety: "READ_ONLY",
        },
        repository_scope: { kind: "EXPLICIT", repository_ids: ["repo-a"] },
        targets: [{ repository_id: "repo-a", name: "Repo A", path: "/work/repo-a" }],
        accepted_at: "2026-08-26T09:00:00Z",
        progress: { total: 1, succeeded: 0, failed: 0, completed: 0, needs_attention: 0 },
        status: "QUEUED",
        source: "SHARED_RUN",
        target: null,
      },
    }];

    render(<RunHistory
      items={loaded}
      selected={null}
      loading={false}
      detailLoading={false}
      hasMore
      summary={null}
      actionsDisabled={false}
      filter="failed"
      onSelect={vi.fn()}
      onLoadMore={onLoadMore}
      onClearSelection={vi.fn()}
      onRunAgain={vi.fn()}
      onReadmit={vi.fn()}
      onOpenRecovery={vi.fn()}
    />);

    expect(screen.getByRole("region", { name: "Run history" })).toBeTruthy();
    expect(screen.getByText("No loaded runs match this view.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Load older runs" }));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });
});