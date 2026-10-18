import type { OperatorWhyNotDiagnostic } from "@generated/models";

export function fixtureLockDriftDiagnostic(): OperatorWhyNotDiagnostic {
  return {
    state: "BLOCKED",
    reason_code: "LOCK_DRIFT",
    authority_owner: "goon.lock",
    current_state: "resolved execution identity differs from the reviewed lock",
    required_state: "goon.lock matches the resolved execution identity",
    retryable: false,
    safe_next_action: "Review and refresh goon.lock before running the Shared Plan",
  };
}
