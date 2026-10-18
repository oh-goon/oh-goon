import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import { executeOperation } from "@/app/api/httpTransport";
import { ApiError, ApiUnavailableError } from "@/domain/api/ApiError";
import {
  operations,
  operationUrl,
  type OperationCall,
  type OperationName,
  type OperationResponse,
} from "@generated/operations";
import {
  MutationBusyError,
  type MutationController,
  type MutationScope,
} from "@/domain/concurrency/MutationController";
import type { WriteCommandResult, WriteErrorRecovery } from "@/domain/mutations/WriteCommandResult";

export function writeOperation<Name extends OperationName>(
  mutations: MutationController,
  scope: MutationScope,
  name: Name,
  call: OperationCall<Name>,
): Promise<WriteCommandResult<OperationResponse<Name>>> {
  const metadata = operations[name];
  const source = `${metadata.method} ${operationUrl(name, call)}`;
  return writeCommand(mutations, scope, source, () => executeOperation(name, call));
}

async function writeCommand<Result>(
  mutations: MutationController,
  scope: MutationScope,
  source: string,
  command: () => Promise<Result>,
): Promise<WriteCommandResult<Result>> {
  try {
    const execution = await mutations.execute(scope, command);
    return {
      outcome: "saved",
      value: execution.value,
      causalityRevision: execution.causalityRevision,
    };
  } catch (error) {
    const diagnostic = appDiagnosticStore.record({
      source,
      error,
      scope,
      causalityRevision: mutations.causality.current(),
    });
    if (isStaleWrite(error)) {
      mutations.reportConflict(scope);
      return { outcome: "stale", diagnosticId: diagnostic.id };
    }
    return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: writeErrorRecovery(error) };
  }
}

function isStaleWrite(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "CONFLICT" && !error.retryable;
}

function writeErrorRecovery(error: unknown): WriteErrorRecovery {
  if (error instanceof MutationBusyError) return "retry";
  if (error instanceof ApiUnavailableError) return "refresh";
  if (error instanceof ApiError && error.retryable) return "retry";
  return "blocked";
}
