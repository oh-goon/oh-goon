import { FleetRunStatus } from "@generated/enums";
import type { FleetRunState } from "@generated/models";

export type GameTapeRecoveryAction = "readmit-placement" | "run-same-target" | "open-recovery" | "none";

const RECOVERY_INSPECTION_FAILURES = new Set([
  "REPOSITORY_EFFECT_OUTCOME_UNKNOWN",
  "RUN_PARTIALLY_COMMITTED_BY_RESTART",
]);

export function gameTapeRecoveryAction(run: FleetRunState, rerunSupported: boolean): GameTapeRecoveryAction {
  if (run.status !== FleetRunStatus.FAILED) return "none";
  if (run.failure.code === "EXECUTOR_PLACEMENT_LOST") return "readmit-placement";
  if (RECOVERY_INSPECTION_FAILURES.has(run.failure.code)) return "open-recovery";
  if (!rerunSupported) return "none";
  return "run-same-target";
}

export function sameRunRepositoryIds(run: FleetRunState): string[] {
  return run.targets.map((target) => target.repository_id);
}
