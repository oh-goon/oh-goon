import { GameTapeStatus } from "@generated/enums";
import type {
  GameTape,
  GameTapeRepositoryExecution,
  GameTapeStepExecution,
} from "@generated/models";

export interface GameTapeStepContext {
  readonly repository: GameTapeRepositoryExecution;
  readonly step: GameTapeStepExecution;
}

export function gameTapeStepContexts(tape: GameTape): readonly GameTapeStepContext[] {
  return tape.repositories.flatMap((repository) =>
    (repository.steps ?? []).map((step) => ({ repository, step })),
  );
}

export function findGameTapeStepContext(
  contexts: readonly GameTapeStepContext[],
  stepExecutionId: string | null,
): GameTapeStepContext | null {
  if (!stepExecutionId) return null;
  return contexts.find(({ step }) => step.step_execution_id === stepExecutionId) ?? null;
}

export function gameTapeFailureContexts(contexts: readonly GameTapeStepContext[]): readonly GameTapeStepContext[] {
  return contexts.filter(({ step }) => step.status === GameTapeStatus.FAILED || step.status === GameTapeStatus.INTERRUPTED || step.failure !== null && step.failure !== undefined);
}

export function gameTapeFailureNavigation(contexts: readonly GameTapeStepContext[], selectedStepExecutionId: string | null): { previous: GameTapeStepContext | null; next: GameTapeStepContext | null } {
  const failures = gameTapeFailureContexts(contexts);
  if (failures.length === 0) return { previous: null, next: null };
  const selectedIndex = failures.findIndex(({ step }) => step.step_execution_id === selectedStepExecutionId);
  if (selectedIndex < 0) return { previous: null, next: failures[0] ?? null };
  return { previous: failures[selectedIndex - 1] ?? null, next: failures[selectedIndex + 1] ?? null };
}
