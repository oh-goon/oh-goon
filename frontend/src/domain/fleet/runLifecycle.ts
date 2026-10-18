import { FleetRunStatus } from "@generated/enums";
import type { FleetRunFailure, FleetRunState } from "@generated/models";

export function isFleetRunTerminal(run: FleetRunState): boolean {
  return run.status === FleetRunStatus.COMPLETED
    || run.status === FleetRunStatus.CANCELLED
    || run.status === FleetRunStatus.FAILED;
}

export function canRequestFleetRunCancel(run: FleetRunState): boolean {
  return run.status === FleetRunStatus.QUEUED || run.status === FleetRunStatus.RUNNING;
}

export function isFleetRunCancelling(run: FleetRunState): boolean {
  return run.status === FleetRunStatus.CANCELLING;
}

export function fleetRunStartedAt(run: FleetRunState): string | null {
  return run.status === FleetRunStatus.QUEUED ? null : run.started_at ?? null;
}

export function fleetRunFinishedAt(run: FleetRunState): string | null {
  if (run.status === FleetRunStatus.COMPLETED
    || run.status === FleetRunStatus.CANCELLED
    || run.status === FleetRunStatus.FAILED) {
    return run.finished_at;
  }
  return null;
}

export function fleetRunFailure(run: FleetRunState): FleetRunFailure | null {
  return run.status === FleetRunStatus.FAILED ? run.failure : null;
}

export function runPollDelay(failures: number, baseMilliseconds = 400): number {
  return Math.min(5_000, baseMilliseconds * 2 ** Math.min(failures, 4));
}
