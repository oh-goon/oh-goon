import type { FleetExecutionTimeline } from "@generated/models";

export type ExecutionObservationBucket = FleetExecutionTimeline["bucket"];

export function executionObservationWindow(bucket: ExecutionObservationBucket, now = new Date()): Readonly<{ fromAt: string; toAt: string }> {
  const current = new Date(now.getTime());
  if (bucket === "day") {
    const end = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), current.getUTCDate() + 1));
    const start = new Date(end.getTime() - 14 * 24 * 60 * 60 * 1000);
    return { fromAt: start.toISOString(), toAt: end.toISOString() };
  }
  if (bucket === "week") {
    const day = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth(), current.getUTCDate()));
    const daysUntilMonday = (8 - (day.getUTCDay() || 7)) % 7;
    const end = new Date(day.getTime() + (daysUntilMonday || 7) * 24 * 60 * 60 * 1000);
    const start = new Date(end.getTime() - 12 * 7 * 24 * 60 * 60 * 1000);
    return { fromAt: start.toISOString(), toAt: end.toISOString() };
  }
  const end = new Date(Date.UTC(current.getUTCFullYear(), current.getUTCMonth() + 1, 1));
  const start = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - 12, 1));
  return { fromAt: start.toISOString(), toAt: end.toISOString() };
}

export function nextExecutionObservationBoundary(bucket: ExecutionObservationBucket, now = new Date()): Date {
  if (bucket === "day") {
    return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
  }
  if (bucket === "week") {
    const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    const daysUntilMonday = (8 - (day.getUTCDay() || 7)) % 7;
    return new Date(day.getTime() + (daysUntilMonday || 7) * 24 * 60 * 60 * 1000);
  }
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
}
