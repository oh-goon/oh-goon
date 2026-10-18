import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import { ApiError } from "@/domain/api/ApiError";
import type { CausalityRevision } from "@/domain/concurrency/CausalityClock";
import type { ReadScope } from "@/domain/reads/readResources";
import type { ReadFailure } from "@/domain/reads/ReadResource";

export function recordReadFailure(
  source: string,
  error: unknown,
  message: string,
  scope: ReadScope,
  causalityRevision: CausalityRevision,
): ReadFailure {
  const diagnostic = appDiagnosticStore.record({ source, error, scope, causalityRevision });
  return {
    diagnosticId: diagnostic.id,
    message,
    recoverable: !(error instanceof ApiError) || error.retryable,
  };
}
