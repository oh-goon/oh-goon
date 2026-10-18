import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import { useReadResource } from "@/app/reads/useReadResource";
import type { CancellationRequestedPlacementQueueEntry, FleetExecutorPoolSnapshot, FleetExecutorStatus, PlacementQueueSnapshot, WaitingPlacementQueueEntry } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

type PlacementQueueEntry = WaitingPlacementQueueEntry | CancellationRequestedPlacementQueueEntry;

const EXECUTOR_OPERATIONS_POLL_MS = 3_000;

export interface ExecutorOperationsState {
  executors: ReadResource<FleetExecutorPoolSnapshot>;
  placementQueue: ReadResource<PlacementQueueSnapshot>;
  refresh(): Promise<void>;
  drain(executor: FleetExecutorStatus): Promise<void>;
  activate(executor: FleetExecutorStatus): Promise<void>;
  revokeTrust(executor: FleetExecutorStatus): Promise<void>;
  restoreTrust(executor: FleetExecutorStatus): Promise<void>;
  cancelQueued(entry: PlacementQueueEntry): Promise<void>;
}

export function useExecutorOperations(enabled: boolean): ExecutorOperationsState {
  const client = useApiClient();
  const reportMutation = useReportMutationFeedback("fleet-run");
  const { t } = useTranslation();
  const executorRequest = useCallback((signal: AbortSignal) => client.executorStatus({ signal }), [client]);
  const queueRequest = useCallback((signal: AbortSignal) => client.placementQueue({ signal }), [client]);
  const executorRead = useReadResource({
    scope: "executor-operations",
    source: "GET /fleet/executors",
    enabled,
    errorMessage: t("operationsTool.readError"),
    request: executorRequest,
    pollIntervalMs: EXECUTOR_OPERATIONS_POLL_MS,
  });
  const queueRead = useReadResource({
    scope: "executor-placement-queue",
    source: "GET /fleet/executors/placement-queue",
    enabled,
    errorMessage: t("operationsTool.readError"),
    request: queueRequest,
    pollIntervalMs: EXECUTOR_OPERATIONS_POLL_MS,
  });

  const refresh = useCallback(async () => {
    await Promise.all([executorRead.refresh(), queueRead.refresh()]);
  }, [executorRead, queueRead]);

  const reportExecutorMutation = useCallback(async (
    executor: FleetExecutorStatus,
    operation: string,
    expectedAdmissionMode: "active" | "draining" | null,
    expectedTrustState: "trusted" | "revoked" | null,
    request: () => Promise<WriteCommandResult<FleetExecutorStatus>>,
  ) => {
    const result = validateExecutorMutation(await request(), executor, operation, expectedAdmissionMode, expectedTrustState);
    reportMutation(result);
  }, [reportMutation]);

  const reportQueueCancellation = useCallback(async (entry: PlacementQueueEntry) => {
    const result = validateQueueCancellation(
      await client.cancelQueuedAdmission(entry.fleet_run_id, entry.sequence),
      entry,
    );
    reportMutation(result);
  }, [client, reportMutation]);

  return {
    executors: executorRead.resource,
    placementQueue: queueRead.resource,
    refresh,
    drain: (executor) => reportExecutorMutation(executor, "drain", "draining", null, () => client.drainExecutor(executor.executor_id, executor.generation, executor.operational_revision)),
    activate: (executor) => reportExecutorMutation(executor, "activate", "active", null, () => client.activateExecutor(executor.executor_id, executor.generation, executor.operational_revision)),
    revokeTrust: (executor) => reportExecutorMutation(executor, "revoke-trust", null, "revoked", () => client.revokeExecutorTrust(executor.executor_id, executor.generation, executor.operational_revision)),
    restoreTrust: (executor) => reportExecutorMutation(executor, "restore-trust", null, "trusted", () => client.restoreExecutorTrust(executor.executor_id, executor.generation, executor.operational_revision)),
    cancelQueued: reportQueueCancellation,
  };
}

function validateExecutorMutation(
  result: WriteCommandResult<FleetExecutorStatus>,
  expected: FleetExecutorStatus,
  operation: string,
  expectedAdmissionMode: "active" | "draining" | null,
  expectedTrustState: "trusted" | "revoked" | null,
): WriteCommandResult<FleetExecutorStatus> {
  if (result.outcome !== "saved") return result;
  const value = result.value;
  const identityMatches = value.executor_id === expected.executor_id && value.generation === expected.generation;
  const stateMatches = (expectedAdmissionMode === null || value.admission_mode === expectedAdmissionMode)
    && (expectedTrustState === null || value.trust_state === expectedTrustState);
  if (identityMatches && stateMatches && value.operational_revision >= expected.operational_revision) return result;
  const diagnostic = appDiagnosticStore.record({
    source: "executor-operation-identity",
    error: new Error(`Executor ${operation} result does not match command owner ${expected.executor_id}/${expected.generation}@${expected.operational_revision}`),
    scope: "fleet-run",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}

function validateQueueCancellation(
  result: WriteCommandResult<PlacementQueueEntry>,
  expected: PlacementQueueEntry,
): WriteCommandResult<PlacementQueueEntry> {
  if (result.outcome !== "saved" || (
    result.value.fleet_run_id === expected.fleet_run_id
    && result.value.sequence === expected.sequence
    && result.value.state === "cancellation_requested"
  )) return result;
  const diagnostic = appDiagnosticStore.record({
    source: "queue-cancel-identity",
    error: new Error(`Queue cancellation result does not match ${expected.fleet_run_id}/${expected.sequence}`),
    scope: "fleet-run",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}
