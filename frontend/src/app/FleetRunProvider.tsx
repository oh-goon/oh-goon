import type { SharedPlanInputs } from "@/domain/api/ApiClient";
import { createContext, type Context, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import { useApiClient, useReadController, useReportMutationFeedback } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { useAppBootstrap } from "@/app/bootstrap/AppBootstrapProvider";
import type { FleetRunOptions } from "@/domain/api/ApiClient";
import type { FleetRunDetail, FleetRunOverview, FleetRunState, FleetSnapshot, OperationAction, RunAgentSelection } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import { readScopesForResources, readScopesInvalidatedByFleetProgress } from "@/domain/reads/readResources";
import { watchFleetRun } from "@/app/fleetRun/watchFleetRun";
import { bindFleetRunDetails, projectFleetRunOwnership, retainCompletedLocalRun, type LocalRunObservation } from "@/app/fleetRun/runStatusProjection";
import { newFleetRunId } from "@/domain/identity/commandIdentity";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { ReadResource } from "@/domain/reads/ReadResource";
import { contentRevision, fleetSnapshotRevision } from "@/domain/reads/readRevision";
import { isFleetRunTerminal } from "@/domain/fleet/runLifecycle";
import { PendingCommandIdentitySet } from "@/domain/concurrency/PendingCommandIdentitySet";
import { hasExpectedSavedRunIdentity } from "@/app/fleetRun/runMutationHandoff";


const RUNTIME_DISCOVERY_POLL_MS = 5_000;

interface FleetRunStatusValue {
  /** Current non-terminal execution identity: durable backend projection with a local event-stream overlay. */
  activeRun: FleetRunState | null;
  /** Detail only for activeRun; never an unrelated or previously completed Run. */
  activeRunDetail: FleetRunDetail | null;
  /** Terminal state from the locally watched execution handoff; not global history/latest truth. */
  completedLocalRun: FleetRunState | null;
  /** Final detail for completedLocalRun when the detail read succeeds. */
  completedLocalRunDetail: FleetRunDetail | null;
  /** Observation/detail error only for activeRun. */
  activeRunError: string | null;
  /** Detail enrichment error only for completedLocalRun. */
  completedLocalRunError: string | null;
}

export interface FleetSnapshotState {
  resource: ReadResource<FleetSnapshot>;
  refresh(): Promise<FleetSnapshot | null>;
}

interface FleetRunActionsValue {
  runFleet(action: OperationAction, options?: FleetRunOptions): Promise<WriteCommandResult<FleetRunState>>;
  runTeam(repository: string, expectedPipelineSha256: string, expectedRepositoryInstanceId: string, inputs?: Record<string, string>): Promise<WriteCommandResult<FleetRunState>>;
  runSharedPlan(
    planKey: string,
    repositoryIds: string[],
    expectedPlanId: string,
    expectedPlanRevision: string,
    expectedExecutionDigest: string,
    repositoryInstanceIds: Record<string, string>,
    inputs?: SharedPlanInputs,
    agent?: RunAgentSelection | null,
  ): Promise<WriteCommandResult<FleetRunState>>;
  cancelFleet(runId: string): Promise<WriteCommandResult<FleetRunState>>;
  readmitFleet(runId: string): Promise<WriteCommandResult<FleetRunState>>;
  rerunFleet(runId: string): Promise<WriteCommandResult<FleetRunState>>;
}

const StatusContext = createContext<FleetRunStatusValue | null>(null);
const ActionsContext = createContext<FleetRunActionsValue | null>(null);
const FleetContext = createContext<FleetSnapshotState | null>(null);

export function FleetRunProvider({ children }: { children: ReactNode }) {
  const client = useApiClient();
  const reads = useReadController();
  const bootstrap = useAppBootstrap();
  const reportMutationFeedback = useReportMutationFeedback("fleet-run");
  const { t } = useTranslation();
  const bootstrapFleet = bootstrap.state.status === "boot-ready" ? bootstrap.state.fleet : undefined;
  const requestFleet = useCallback((signal: AbortSignal) => client.fleet({ signal }), [client]);
  const fleet = useReadResource({
    scope: "fleet",
    source: "GET /fleet",
    errorMessage: t("errors.refresh"),
    request: requestFleet,
    revision: fleetSnapshotRevision,
    initialValue: bootstrapFleet,
  });
  const requestRuntimeDiscovery = useCallback((signal: AbortSignal) => client.fleetRunOverview({ signal }), [client]);
  const runtimeDiscovery = useReadResource<FleetRunOverview>({
    scope: "runtime-discovery",
    source: "GET /fleet/runs/overview runtime discovery",
    errorMessage: t("errors.refresh"),
    request: requestRuntimeDiscovery,
    revision: contentRevision,
    pollIntervalMs: RUNTIME_DISCOVERY_POLL_MS,
  });
  const observedRuntimeRevision = useRef<string | null>(null);
  useEffect(() => {
    const revision = runtimeDiscovery.resource.revision;
    if (revision === null || revision === observedRuntimeRevision.current) return;
    const previous = observedRuntimeRevision.current;
    observedRuntimeRevision.current = revision;
    // The first discovery read validates bootstrap truth once. Later changes are
    // external durable progress (scheduler/another client/executor) and must
    // invalidate every projection derived from Fleet Run progress. The
    // runtime-discovery scope uses its own resource, so this cannot self-loop.
    if (previous !== null || runtimeDiscovery.resource.data !== null) {
      reads.invalidateMany(readScopesInvalidatedByFleetProgress());
    }
  }, [reads, runtimeDiscovery.resource.data, runtimeDiscovery.resource.revision]);
  const [liveRun, setLiveRun] = useState<LocalRunObservation | null>(null);
  const [localTerminalRun, setLocalTerminalRun] = useState<FleetRunState | null>(null);
  const [runError, setRunError] = useState<{ runId: string; message: string } | null>(null);
  const watchedRun = useRef<string | null>(null);
  const pendingStarts = useRef<PendingCommandIdentitySet | null>(null);
  if (pendingStarts.current === null) pendingStarts.current = new PendingCommandIdentitySet(newFleetRunId);


  const observedActiveDetail = fleet.resource.data === null
    ? (bootstrapFleet?.active_fleet_run ?? null)
    : (fleet.resource.data.active_fleet_run ?? null);
  const observedActiveKnown = fleet.resource.data !== null || bootstrapFleet !== undefined;
  const observedCausalityRevision = fleet.resource.data !== null
    ? fleet.resource.causalityRevision
    : bootstrapFleet !== undefined
      ? reads.causality.current()
      : null;
  const observedJournalRevision = fleet.resource.data !== null
    ? fleet.resource.data.provenance.journal_revision
    : bootstrapFleet?.provenance.journal_revision ?? null;
  const ownership = projectFleetRunOwnership({
    liveRun,
    observedActiveDetail,
    observedActiveKnown,
    observedCausalityRevision,
    observedJournalRevision,
    localTerminalRun,
  });
  const detailRunId = ownership.completedLocalRun?.fleet_run_id ?? null;
  const requestRunDetail = useCallback((signal: AbortSignal) => {
    if (detailRunId === null) return Promise.reject(new Error("Fleet Run detail requested without an owned Run identity"));
    return client.fleetRunDetail(detailRunId, { signal });
  }, [client, detailRunId]);
  const runDetail = useReadResource({
    scope: "fleet-run-detail",
    source: detailRunId === null ? "GET /fleet/runs/{run}" : `GET /fleet/runs/${detailRunId}`,
    identity: detailRunId,
    enabled: detailRunId !== null,
    errorMessage: t("errors.runDetail"),
    request: requestRunDetail,
    validate: (value) => {
      if (detailRunId === null || value.run.fleet_run_id !== detailRunId) {
        throw new Error(`Fleet Run completion detail identity mismatch: requested ${detailRunId ?? "none"}, received ${value.run.fleet_run_id}`);
      }
    },
  });
  const { activeRun, activeRunDetail, completedLocalRun, completedLocalRunDetail } = bindFleetRunDetails({
    ...ownership,
    completedRunDetail: runDetail.resource.data,
    observedActiveDetail,
  });

  useEffect(() => {
    const durableActiveRunId = observedActiveDetail !== null && !isFleetRunTerminal(observedActiveDetail.run)
      ? observedActiveDetail.run.fleet_run_id
      : null;
    if (durableActiveRunId === null) return;
    setLocalTerminalRun((current) => retainCompletedLocalRun(current, durableActiveRunId));
  }, [observedActiveDetail]);

  useEffect(() => {
    const snapshot = fleet.resource.data;
    if (snapshot === null) return;
    pendingStarts.current!.observeDurableIdentities([
      ...(snapshot.active_fleet_run ? [snapshot.active_fleet_run.run.fleet_run_id] : []),
      ...(snapshot.latest_fleet_runs ?? []).map((detail) => detail.run.fleet_run_id),
    ]);
  }, [fleet.resource.revision]);

  const pendingFleetRunId = useCallback((key: string): string => pendingStarts.current!.forCommand(key), []);

  const acceptStartedRun = useCallback((
    key: string,
    expectedRunId: string | null,
    result: WriteCommandResult<FleetRunState>,
  ): WriteCommandResult<FleetRunState> => {
    // A saved write is causally real even when its response violates the command
    // identity contract. Observe/invalidate first, then fail closed for ownership.
    reportMutationFeedback(result);
    const accepted = expectedRunId === null ? result : validateSavedRunIdentity(result, expectedRunId, "start");
    if (accepted !== result) reportMutationFeedback(accepted);
    pendingStarts.current!.settle(key, accepted);
    if (accepted.outcome !== "saved") return accepted;
    const run = accepted.value;
    if (watchedRun.current !== null && watchedRun.current !== run.fleet_run_id) {
      // A newer saved Run identity revokes the previous watch immediately. The
      // old effect may still have an in-flight response until React runs its
      // cleanup, but its callbacks must no longer be allowed to own state.
      watchedRun.current = null;
    }
    setLiveRun({ run, causalityRevision: accepted.causalityRevision, journalRevision: null });
    setLocalTerminalRun(null);
    setRunError(null);
    return accepted;
  }, [reportMutationFeedback]);

  const runFleet = useCallback(async (action: OperationAction, options?: FleetRunOptions) => {
    const key = canonicalIdentity({
      action,
      target: options?.target ?? null,
      repositoryIds: [...(options?.repositoryIds ?? [])].sort(),
      repositoryInstanceIds: options?.repositoryInstanceIds ?? null,
    });
    const fleetRunId = pendingFleetRunId(key);
    const result = await client.startFleetRun(fleetRunId, action, options);
    return acceptStartedRun(key, fleetRunId, result);
  }, [acceptStartedRun, client, pendingFleetRunId]);

  const cancelFleet = useCallback(async (runId: string) => {
    const result = await client.cancelFleetRun(runId);
    reportMutationFeedback(result);
    const accepted = validateSavedRunIdentity(result, runId, "cancel");
    if (accepted !== result) reportMutationFeedback(accepted);
    if (accepted.outcome === "saved") {
      if (isFleetRunTerminal(accepted.value)) {
        setLocalTerminalRun(accepted.value);
        setLiveRun(null);
      } else {
        setLiveRun({ run: accepted.value, causalityRevision: accepted.causalityRevision, journalRevision: null });
      }
      setRunError((current) => current?.runId === runId ? null : current);
    }
    return accepted;
  }, [client, reportMutationFeedback]);

  const readmitFleet = useCallback(async (runId: string) => {
    const key = canonicalIdentity({ readmitFleet: runId });
    const result = await client.readmitFleetRun(runId, pendingFleetRunId(key));
    if (result.outcome !== "saved") {
      return acceptStartedRun(key, null, result);
    }
    const childResult: WriteCommandResult<FleetRunState> = {
      outcome: "saved",
      value: result.value,
      causalityRevision: result.causalityRevision,
    };
    // Readmission is parent-owned: backend may return the already durable child
    // after reload even when this client proposed a fresh command identity.
    return acceptStartedRun(key, null, childResult);
  }, [acceptStartedRun, client, pendingFleetRunId, reportMutationFeedback]);

  const rerunFleet = useCallback(async (runId: string) => {
    const key = canonicalIdentity({ rerunFleet: runId });
    const fleetRunId = pendingFleetRunId(key);
    const result = await client.rerunFleetRun(runId, fleetRunId);
    return acceptStartedRun(key, fleetRunId, result);
  }, [acceptStartedRun, client, pendingFleetRunId]);

  const runTeam = useCallback(async (repository: string, expectedPipelineSha256: string, expectedRepositoryInstanceId: string, inputs: Record<string, string> = {}) => {
    const key = canonicalIdentity({ team: repository, expectedPipelineSha256, expectedRepositoryInstanceId, inputs });
    const fleetRunId = pendingFleetRunId(key);
    const result = await client.startTeamRun(fleetRunId, repository, expectedPipelineSha256, expectedRepositoryInstanceId, inputs);
    return acceptStartedRun(key, fleetRunId, result);
  }, [acceptStartedRun, client, pendingFleetRunId]);

  const runSharedPlan = useCallback(async (
    planKey: string,
    repositoryIds: string[],
    expectedPlanId: string,
    expectedPlanRevision: string,
    expectedExecutionDigest: string,
    repositoryInstanceIds: Record<string, string>,
    inputs: SharedPlanInputs = {},
    agent?: RunAgentSelection | null,
  ) => {
    const key = canonicalIdentity({
      internalPlan: planKey,
      repositoryIds: [...repositoryIds].sort(),
      expectedPlanId,
      expectedPlanRevision,
      expectedExecutionDigest,
      repositoryInstanceIds,
      inputs,
      agent: agent ?? null,
    });
    const fleetRunId = pendingFleetRunId(key);
    const result = await client.startSharedPlanRun(
      fleetRunId,
      planKey,
      repositoryIds,
      expectedPlanId,
      expectedPlanRevision,
      expectedExecutionDigest,
      repositoryInstanceIds,
      inputs,
      agent,
    );
    return acceptStartedRun(key, fleetRunId, result);
  }, [acceptStartedRun, client, pendingFleetRunId]);

  useEffect(() => {
    const runId = activeRun?.fleet_run_id;
    if (runId === undefined || watchedRun.current === runId) return;
    watchedRun.current = runId;
    const ownsWatch = () => watchedRun.current === runId;
    const stop = watchFleetRun(client, reads, runId, t("errors.operationProgress"), {
      onBatch: (run, causalityRevision, journalRevision) => { if (ownsWatch()) setLiveRun({ run, causalityRevision, journalRevision }); },
      onProgress: () => { if (ownsWatch()) reads.invalidateMany(readScopesInvalidatedByFleetProgress()); },
      onError: () => { if (ownsWatch()) setRunError({ runId, message: t("errors.operationProgress") }); },
      onInvariant: () => { if (ownsWatch()) reads.invalidate("fleet"); },
      onHealthy: () => { if (ownsWatch()) setRunError((current) => current?.runId === runId ? null : current); },
      onTerminal: (terminalRun) => {
        if (!ownsWatch()) return;
        setLocalTerminalRun(terminalRun);
        setLiveRun(null);
        watchedRun.current = null;
        reads.invalidateMany(readScopesForResources(["league-map"]));
      },
    });
    return () => {
      stop();
      if (watchedRun.current === runId) watchedRun.current = null;
    };
  }, [activeRun?.fleet_run_id, client, reads, t]);

  return (
    <FleetContext.Provider value={{ resource: fleet.resource, refresh: fleet.refresh }}>
      <StatusContext.Provider value={{
        activeRun,
        activeRunDetail,
        completedLocalRun,
        completedLocalRunDetail,
        activeRunError: activeRun !== null && runError?.runId === activeRun.fleet_run_id ? runError.message : null,
        completedLocalRunError: completedLocalRun !== null && activeRun === null
          ? runDetail.resource.failure?.message ?? null
          : null,
      }}>
        <ActionsContext.Provider value={{ runFleet, runTeam, runSharedPlan, cancelFleet, readmitFleet, rerunFleet }}>
          {children}
        </ActionsContext.Provider>
      </StatusContext.Provider>
    </FleetContext.Provider>
  );
}

function validateSavedRunIdentity(
  result: WriteCommandResult<FleetRunState>,
  expectedRunId: string,
  operation: string,
): WriteCommandResult<FleetRunState> {
  if (result.outcome !== "saved") return result;
  if (hasExpectedSavedRunIdentity(result, expectedRunId)) return result;
  const diagnostic = appDiagnosticStore.record({
    source: `fleet-run-${operation}-identity`,
    error: new Error(`Fleet Run mutation identity mismatch: expected ${expectedRunId}, received ${result.value.fleet_run_id}`),
    scope: "fleet-run",
    causalityRevision: result.causalityRevision,
  });
  return { outcome: "write-error", diagnosticId: diagnostic.id, recovery: "refresh" };
}


export function useFleetSnapshot(): FleetSnapshotState {
  return useRequiredContext(FleetContext, "FleetRunProvider fleet projection");
}

export function useFleetRunStatus(): FleetRunStatusValue {
  return useRequiredContext(StatusContext, "FleetRunProvider status");
}

export function useFleetRunActions(): FleetRunActionsValue {
  return useRequiredContext(ActionsContext, "FleetRunProvider actions");
}

function useRequiredContext<Value>(context: Context<Value | null>, name: string): Value {
  const value = useContext(context);
  if (value === null) throw new Error(`${name} is missing`);
  return value;
}
