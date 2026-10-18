import { describe, expect, it } from "vitest";

import type { FleetRunHistoryItem, FleetRunHistoryPage, GameTapeLiveOutputBatch } from "@generated/models";
import { validateLiveOutputBatch } from "@/features/game-tape/hooks/useGameTapeLiveOutput";
import { validateHistoryContinuation } from "@/domain/fleet/historyContinuation";

function liveBatch(nextSequence: number, sequences: number[]): GameTapeLiveOutputBatch {
  return {
    fleet_run_id: "run-a",
    state: "ACTIVE",
    available: true,
    expires_at: null,
    chunks: sequences.map((sequence) => ({
      sequence,
      repository_id: "repo-a",
      step_id: "test",
      attempt: 1,
      stream: "stdout",
      line_start: sequence,
      line_end: sequence,
      text: `line ${sequence}`,
      emitted_at: "2026-08-29T10:00:00Z",
    })),
    failure_anchors: [],
    failure_navigation_state: "AVAILABLE",
    next_sequence: nextSequence,
    retained_from_sequence: sequences[0] ?? nextSequence,
    dropped_chunk_count: 0,
  } as GameTapeLiveOutputBatch;
}

function historyItem(runId: string, revision: number): FleetRunHistoryItem {
  return { admitted_revision: revision, run: { fleet_run_id: runId } } as FleetRunHistoryItem;
}

describe("cross-boundary protocol lineage", () => {
  it("rejects live-output cursor regression instead of clamping it", () => {
    const current = liveBatch(30, [29, 30]);
    const incoming = liveBatch(25, []);
    expect(() => validateLiveOutputBatch(30, current, incoming)).toThrow(/cursor regression|regressed/);
  });

  it("rejects live-output replay at or before the requested cursor", () => {
    expect(() => validateLiveOutputBatch(30, liveBatch(30, [30]), liveBatch(31, [30, 31]))).toThrow(/replay/);
  });

  it("rejects a history continuation that crosses its requested boundary", () => {
    const current = [historyItem("run-a", 100), historyItem("run-b", 90)];
    const page = { items: [historyItem("run-c", 95)], journal_revision: 110, next_before_revision: null } as FleetRunHistoryPage;
    expect(() => validateHistoryContinuation(current, 90, page)).toThrow(/crossed requested cursor/);
  });

  it("rejects a duplicate Run across history pages instead of silently deduplicating it", () => {
    const current = [historyItem("run-a", 100), historyItem("run-b", 90)];
    const page = { items: [historyItem("run-a", 80)], journal_revision: 110, next_before_revision: null } as FleetRunHistoryPage;
    expect(() => validateHistoryContinuation(current, 90, page)).toThrow(/repeated Run/);
  });
});
