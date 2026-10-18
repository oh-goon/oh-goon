import type { GameTape, ResolvedExecutionReference } from "@generated/models";

export function gameTapeExecutionReference(tape: GameTape): ResolvedExecutionReference | null {
  return tape.execution_reference ?? null;
}

export function gameTapeSourceLabel(reference: ResolvedExecutionReference): string {
  if (reference.source.kind === "team_goon") return "Repository goon.yml";
  if (reference.source.kind === "shared_plan") return `Plan · ${reference.source.plan_key}`;
  return "plan file";
}
