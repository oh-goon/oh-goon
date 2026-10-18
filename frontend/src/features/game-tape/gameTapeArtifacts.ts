import type { GameTape, MissingPlanArtifact, PresentPlanArtifact } from "@generated/models";

export type GameTapeArtifact = MissingPlanArtifact | PresentPlanArtifact;

export function gameTapeArtifactKey(repositoryId: string, stepId: string): string {
  return `${repositoryId}\u0000${stepId}`;
}

export function gameTapeArtifacts(tape: GameTape): Map<string, GameTapeArtifact[]> {
  const artifacts = new Map<string, GameTapeArtifact[]>();
  for (const repository of tape.repositories ?? []) {
    for (const step of repository.steps ?? []) {
      const values = step.artifacts ?? [];
      if (values.length === 0) continue;
      artifacts.set(gameTapeArtifactKey(repository.repository.repository_id, step.step_id), [...values]);
    }
  }
  return artifacts;
}
