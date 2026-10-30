import type { GameTape, GameTapeArtifactAvailability, GameTapeArtifactObservation, MissingPlanArtifact, PresentPlanArtifact } from "@generated/models";

import { canonicalIdentity } from "@/domain/identity/canonicalValue";

export type GameTapeArtifact = MissingPlanArtifact | PresentPlanArtifact;
export type GameTapeArtifactOwner = Pick<GameTapeArtifactAvailability, "repository_execution_id" | "repository_id" | "step_execution_id" | "step_id">;

export function gameTapeArtifactObservationIdentity(tape: GameTape): string {
  return canonicalIdentity({
    runId: tape.fleet_run_id,
    status: tape.status,
    repositories: tape.repositories.map((repository) => ({
      repository_execution_id: repository.repository_execution_id,
      repository_id: repository.repository.repository_id,
      steps: (repository.steps ?? []).map((step) => ({
        step_execution_id: step.step_execution_id,
        step_id: step.step_id,
        status: step.status,
        settlement: step.settlement,
        artifacts: step.artifacts,
      })),
    })),
  });
}

export function matchingArtifactObservation(
  observation: GameTapeArtifactObservation | null | undefined,
  owner: GameTapeArtifactOwner | undefined,
  artifact: GameTapeArtifact,
): GameTapeArtifactAvailability | null {
  if (!observation || !owner) return null;
  const matches = observation.observations.filter((item) =>
    item.repository_execution_id === owner.repository_execution_id
    && item.repository_id === owner.repository_id
    && item.step_execution_id === owner.step_execution_id
    && item.step_id === owner.step_id
    && item.path === artifact.path
    && item.recorded_status === artifact.status
    && (item.recorded_sha256 ?? null) === (artifact.status === "PRESENT" ? artifact.sha256 : null)
    && (item.recorded_byte_count ?? null) === (artifact.status === "PRESENT" ? artifact.byte_count : null)
    && (item.reference_id ?? null) === (artifact.status === "PRESENT" ? artifact.retained_object?.reference_id ?? null : null),
  );
  return matches.length === 1 ? matches[0]! : null;
}

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
