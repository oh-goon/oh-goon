import type { FleetRunState, RunFeedback } from "@generated/models";

export type GameTapeRecoveryAction = RunFeedback["recovery_action"];

export function gameTapeRecoveryAction(feedback: RunFeedback | null | undefined, runId: string, journalRevision?: number | null, status?: FleetRunState["status"], outcome?: string): GameTapeRecoveryAction {
  if (!feedback || feedback.fleet_run_id !== runId
    || journalRevision !== undefined && journalRevision !== null && feedback.journal_revision !== journalRevision
    || status !== undefined && feedback.status !== status
    || outcome !== undefined && feedback.outcome !== outcome) return "none";
  return feedback.recovery_action;
}

export function sameRunRepositoryIds(run: FleetRunState): string[] {
  return run.targets.map((target) => target.repository_id);
}
