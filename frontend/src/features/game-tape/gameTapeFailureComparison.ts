import type { GameTapeFailureHistory } from "@generated/models";

export function gameTapePreviousExactComparableRunId(
  history: GameTapeFailureHistory | null,
  options: readonly { readonly runId: string }[],
): string | null {
  const candidate = history?.occurrences?.[0]?.fleet_run_id ?? null;
  if (!candidate) return null;
  return options.some((option) => option.runId === candidate) ? candidate : null;
}
