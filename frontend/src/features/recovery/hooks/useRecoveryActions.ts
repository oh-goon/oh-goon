import { useCallback } from "react";

import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import type { RecoveryStatus } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

export interface RecoveryActionsState {
  reconcile(operationId: string, expectedRecordVersion: number): Promise<WriteCommandResult<RecoveryStatus>>;
  acknowledge(operationId: string, note: string, expectedRecordVersion: number): Promise<WriteCommandResult<RecoveryStatus>>;
}

export function useRecoveryActions(): RecoveryActionsState {
  const client = useApiClient();
  const report = useReportMutationFeedback("recovery");
  const reconcile = useCallback(async (operationId: string, expectedRecordVersion: number) => {
    const result = validateRecoveryMutationResult(
      await client.reconcileEffect(operationId, expectedRecordVersion),
      operationId,
      expectedRecordVersion,
      "reconcile",
    );
    report(result);
    return result;
  }, [client, report]);
  const acknowledge = useCallback(async (operationId: string, note: string, expectedRecordVersion: number) => {
    const result = validateRecoveryMutationResult(
      await client.acknowledgeEffect(operationId, note, expectedRecordVersion),
      operationId,
      expectedRecordVersion,
      "acknowledge",
    );
    report(result);
    return result;
  }, [client, report]);
  return { reconcile, acknowledge };
}

function validateRecoveryMutationResult(
  result: WriteCommandResult<RecoveryStatus>,
  operationId: string,
  expectedRecordVersion: number,
  command: "reconcile" | "acknowledge",
): WriteCommandResult<RecoveryStatus> {
  if (result.outcome !== "saved") return result;
  const returned = (result.value.effect_quarantines ?? []).find((item) => item.operation_id === operationId);
  const valid = command === "acknowledge"
    ? returned === undefined
    : returned === undefined || returned.record_version > expectedRecordVersion;
  if (valid) return result;
  const diagnostic = appDiagnosticStore.record({
    source: "recovery-mutation-identity",
    error: new Error(`Recovery ${command} result does not advance operation ${operationId}`),
    scope: "recovery",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}
