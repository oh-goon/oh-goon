import { describe, expect, it } from "vitest";
import type { GameTapeStepExecution } from "@generated/models";
import {
  gameTapeElapsedWidthPercent,
  gameTapeNiceElapsedSpan,
  gameTapeSharedElapsedScale,
  gameTapeTimelineInterval,
  gameTapeTimelinePosition,
  gameTapeTimelineScale,
} from "@/features/game-tape/gameTapeTimelineModel";

function step(startedAt: string | null, finishedAt: string | null, durationMs: number | null): GameTapeStepExecution {
  return { started_at: startedAt, finished_at: finishedAt, duration_ms: durationMs } as GameTapeStepExecution;
}

describe("Game Tape timeline model", () => {
  it("uses recorded timestamps for completed Steps", () => {
    const value = gameTapeTimelineInterval(step("2026-08-22T00:00:00Z", "2026-08-22T00:00:12Z", 12_000));
    expect(value).toEqual({ startMs: Date.parse("2026-08-22T00:00:00Z"), endMs: Date.parse("2026-08-22T00:00:12Z") });
  });

  it("uses recorded duration for an open Step without inventing current time", () => {
    const value = gameTapeTimelineInterval(step("2026-08-22T00:00:10Z", null, 5_000));
    expect(value?.endMs).toBe(Date.parse("2026-08-22T00:00:15Z"));
  });

  it("rounds the elapsed domain upward instead of stretching every run to full width", () => {
    expect(gameTapeNiceElapsedSpan(70_000)).toBe(80_000);
    expect(gameTapeNiceElapsedSpan(102_000)).toBe(125_000);
  });

  it("anchors the timeline to recorded Run timing when available", () => {
    const steps = [step("2026-08-22T00:00:10Z", "2026-08-22T00:00:20Z", 10_000)];
    const scale = gameTapeTimelineScale(steps, { startedAt: "2026-08-22T00:00:00Z", finishedAt: "2026-08-22T00:01:10Z", durationMs: 70_000 })!;
    expect(scale.minMs).toBe(Date.parse("2026-08-22T00:00:00Z"));
    expect(scale.spanMs).toBe(80_000);
    expect(scale.ticks).toHaveLength(5);
  });

  it("keeps bar width proportional without inflating very short Steps", () => {
    const steps = [step("2026-08-22T00:00:00Z", "2026-08-22T00:01:20Z", 80_000)];
    const scale = gameTapeTimelineScale(steps)!;
    const tiny = gameTapeTimelineInterval(step("2026-08-22T00:00:10Z", "2026-08-22T00:00:10.100Z", 100))!;
    expect(gameTapeTimelinePosition(tiny, scale).widthPercent).toBeCloseTo(0.125);
  });

  it("uses one shared elapsed scale for compared Run durations", () => {
    const scale = gameTapeSharedElapsedScale([30_000, 60_000])!;
    expect(scale.spanMs).toBe(60_000);
    expect(gameTapeElapsedWidthPercent(30_000, scale)).toBe(50);
    expect(gameTapeElapsedWidthPercent(60_000, scale)).toBe(100);
  });
});
