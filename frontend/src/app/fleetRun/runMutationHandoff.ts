import type { FleetRunState } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

/**
 * Resolve the Run identity a history review should follow after a child-creating mutation.
 * The backend owns the child identity, while newer user navigation keeps review ownership.
 */
export function reviewChildRunId(
  result: WriteCommandResult<FleetRunState>,
  parentRunId: string,
  currentReviewRunId: string | null,
): string | null {
  if (result.outcome !== "saved") return null;
  if (currentReviewRunId !== parentRunId) return null;
  return result.value.fleet_run_id;
}

/** Ordinary Run-creating/cancel commands must echo the exact identity they were issued for. */
export function hasExpectedSavedRunIdentity(
  result: WriteCommandResult<FleetRunState>,
  expectedRunId: string,
): boolean {
  return result.outcome !== "saved" || result.value.fleet_run_id === expectedRunId;
}
