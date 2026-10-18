import { describe, expect, it } from "vitest";

import type { FleetRunHistoryItem } from "@generated/models";
import {
  formatRunRecordedDuration,
  runFailureSummary,
  runRecordedDurationMs,
  runTargetDisplay,
} from "@/features/history/runHistoryPresentation";

function item(targets: { name: string }[]): FleetRunHistoryItem {
  return { run: { targets } } as unknown as FleetRunHistoryItem;
}

describe("run history presentation", () => {
  it("uses the repository name for one target and caller-owned count copy for a scope", () => {
    expect(runTargetDisplay(item([{ name: "api" }]), "1 repository")).toBe("api");
    expect(runTargetDisplay(item([{ name: "api" }, { name: "web" }]), "2 repositories")).toBe("2 repositories");
  });
});


it("formats only recorded start/finish duration and exposes canonical run failure text", () => {
  const item = { run: { started_at: "2026-08-22T10:00:00Z", finished_at: "2026-08-22T10:01:05Z", failure: { message: "Docker build exited with code 1" }, targets: [] } } as unknown as FleetRunHistoryItem;
  expect(runRecordedDurationMs(item)).toBe(65_000);
  expect(formatRunRecordedDuration(item)).toBe("1m 5s");
  expect(runFailureSummary(item)).toBe("Docker build exited with code 1");
});

it("does not invent duration for an unfinished run", () => {
  const item = { run: { started_at: "2026-08-22T10:00:00Z", targets: [] } } as unknown as FleetRunHistoryItem;
  expect(runRecordedDurationMs(item)).toBeNull();
  expect(formatRunRecordedDuration(item)).toBeNull();
});
