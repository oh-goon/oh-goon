import type { FleetRunHistoryItem } from "@generated/models";

export function formatRunDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function runTargetDisplay(item: FleetRunHistoryItem, repositoryCountLabel: string): string {
  return item.run.targets.length === 1 && item.run.targets[0]
    ? item.run.targets[0].name
    : repositoryCountLabel;
}

export function runRecordedDurationMs(item: FleetRunHistoryItem): number | null {
  const run = item.run;
  if (!("started_at" in run) || !run.started_at || !("finished_at" in run) || !run.finished_at) return null;
  const start = new Date(run.started_at).getTime();
  const finish = new Date(run.finished_at).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(finish)) return null;
  return Math.max(0, finish - start);
}

export function formatRunRecordedDuration(item: FleetRunHistoryItem): string | null {
  const milliseconds = runRecordedDurationMs(item);
  if (milliseconds === null) return null;
  if (milliseconds < 1_000) return `${milliseconds}ms`;
  const seconds = milliseconds / 1_000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.round(seconds % 60);
  return remaining === 0 ? `${minutes}m` : `${minutes}m ${remaining}s`;
}

export function runFailureSummary(item: FleetRunHistoryItem): string | null {
  return "failure" in item.run ? item.run.failure.message : null;
}
