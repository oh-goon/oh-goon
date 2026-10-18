import type { GameTapeStepExecution } from "@generated/models";

export interface GameTapeTimelineInterval {
  startMs: number;
  endMs: number;
}

export interface GameTapeTimelineBounds {
  startedAt?: string | null;
  finishedAt?: string | null;
  durationMs?: number | null;
}

export interface GameTapeTimelineScale {
  minMs: number;
  maxMs: number;
  spanMs: number;
  ticks: readonly number[];
}

export interface GameTapeElapsedScale {
  spanMs: number;
  ticks: readonly number[];
}

const NICE_SPAN_MULTIPLIERS = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10] as const;

export function gameTapeTimelineInterval(step: GameTapeStepExecution): GameTapeTimelineInterval | null {
  if (!step.started_at) return null;
  const startMs = new Date(step.started_at).getTime();
  if (!Number.isFinite(startMs)) return null;
  if (step.finished_at) {
    const endMs = new Date(step.finished_at).getTime();
    return Number.isFinite(endMs) && endMs >= startMs ? { startMs, endMs } : null;
  }
  if (step.duration_ms !== null && step.duration_ms !== undefined && step.duration_ms >= 0) {
    return { startMs, endMs: startMs + step.duration_ms };
  }
  return { startMs, endMs: startMs };
}

export function gameTapeNiceElapsedSpan(spanMs: number): number {
  if (!Number.isFinite(spanMs) || spanMs <= 1) return 1;
  const exponent = Math.floor(Math.log10(spanMs));
  const power = 10 ** exponent;
  const normalized = spanMs / power;
  const multiplier = NICE_SPAN_MULTIPLIERS.find((candidate) => candidate >= normalized) ?? 10;
  return multiplier * power;
}

export function gameTapeTimelineScale(steps: readonly GameTapeStepExecution[], bounds?: GameTapeTimelineBounds): GameTapeTimelineScale | null {
  const intervals = steps.map(gameTapeTimelineInterval).filter((value): value is GameTapeTimelineInterval => value !== null);
  const runInterval = recordedRunInterval(bounds);
  if (intervals.length === 0 && runInterval === null) return null;

  const candidates = runInterval ? [...intervals, runInterval] : intervals;
  const minMs = runInterval?.startMs ?? Math.min(...candidates.map((value) => value.startMs));
  const recordedMaxMs = Math.max(...candidates.map((value) => value.endMs));
  const spanMs = gameTapeNiceElapsedSpan(Math.max(1, recordedMaxMs - minMs));
  const maxMs = minMs + spanMs;
  return { minMs, maxMs, spanMs, ticks: [0, 0.25, 0.5, 0.75, 1].map((fraction) => minMs + spanMs * fraction) };
}

export function gameTapeSharedElapsedScale(values: readonly (number | null | undefined)[]): GameTapeElapsedScale | null {
  const recorded = values.filter((value): value is number => value !== null && value !== undefined && Number.isFinite(value) && value >= 0);
  if (recorded.length === 0) return null;
  const spanMs = gameTapeNiceElapsedSpan(Math.max(1, ...recorded));
  return { spanMs, ticks: [0, 0.25, 0.5, 0.75, 1].map((fraction) => spanMs * fraction) };
}

export function gameTapeTimelinePosition(interval: GameTapeTimelineInterval, scale: GameTapeTimelineScale): { leftPercent: number; widthPercent: number } {
  return {
    leftPercent: ((interval.startMs - scale.minMs) / scale.spanMs) * 100,
    widthPercent: ((interval.endMs - interval.startMs) / scale.spanMs) * 100,
  };
}

export function gameTapeElapsedWidthPercent(value: number | null | undefined, scale: GameTapeElapsedScale): number | null {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0) return null;
  return Math.min(100, (value / scale.spanMs) * 100);
}

function recordedRunInterval(bounds?: GameTapeTimelineBounds): GameTapeTimelineInterval | null {
  if (!bounds?.startedAt) return null;
  const startMs = new Date(bounds.startedAt).getTime();
  if (!Number.isFinite(startMs)) return null;
  if (bounds.finishedAt) {
    const endMs = new Date(bounds.finishedAt).getTime();
    if (Number.isFinite(endMs) && endMs >= startMs) return { startMs, endMs };
  }
  if (bounds.durationMs !== null && bounds.durationMs !== undefined && Number.isFinite(bounds.durationMs) && bounds.durationMs >= 0) {
    return { startMs, endMs: startMs + bounds.durationMs };
  }
  return { startMs, endMs: startMs };
}
