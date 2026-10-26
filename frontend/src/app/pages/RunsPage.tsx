import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { useMutationBusy, usePresentationLocationAuthority } from "@/app/ApiClientProvider";
import { appLocationHash, readLocationHash } from "@/app/navigation";
import { useFleetRunActions, useFleetRunStatus, useFleetSnapshot } from "@/app/FleetRunProvider";
import { reviewChildRunId } from "@/app/fleetRun/runMutationHandoff";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import {
  GameTape,
  gameTapeFlowGroups,
  type GameTapeComparisonOption,
  type GameTapeLogStream,
  type GameTapeView,
} from "@/features/game-tape/GameTapeFeature";
import {
  RunHistory,
  RunReviewPicker,
  formatRunDate,
  runHistoryFilterOutcome,
  runTargetDisplay,
  type RunHistoryFilter,
  useRunDetail,
  useRunHistoryList,
} from "@/features/history/HistoryFeature";
import { actionKey, outcomeKey } from "@/domain/presentation/operations";
import type { FleetRunDetail, FleetRunState } from "@generated/models";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import {
  normalizeRunReviewStateForLoadedRun,
  patchRunReviewState,
  readRunReviewState,
  runReviewHash,
  runReviewUrl,
  writeRunReviewPatch,
  writeRunReviewState,
  type RunReviewCanonicalFacts,
  type RunReviewState,
} from "@/app/pages/runReviewState";

interface RunsPageProps {
  readonly onOpenRecovery: () => void;
  readonly onOpenRepositories: () => void;
  readonly onOpenSharedRuns: () => void;
}

type SelectedRunPatch = Omit<Partial<RunReviewState>, "runId">;

export function RunsPage({
  onOpenRecovery,
  onOpenRepositories,
  onOpenSharedRuns,
}: RunsPageProps) {
  const { t } = useTranslation();
  const runStatus = useFleetRunStatus();
  const runActions = useFleetRunActions();
  const mutationBusy = useMutationBusy();
  const presentationAuthority = usePresentationLocationAuthority();
  const requestedReview = readRunReviewState();
  const history = useRunHistoryList(
    requestedReview.historyOutcome ?? undefined,
    requestedReview.historyStatus ?? undefined,
    requestedReview.historySource ?? undefined,
    requestedReview.historyRepositoryId ?? undefined,
    requestedReview.historyFromAt ?? undefined,
    requestedReview.historyToAt ?? undefined,
    requestedReview.historyPlanQuery || undefined,
  );
  const requestedRunId = requestedReview.runId;
  const detail = useRunDetail(requestedRunId);
  const fleet = useFleetSnapshot();
  const selectedDetail = detail.resource.data;
  const comparisonOptions: GameTapeComparisonOption[] = history.items
    .filter((item) => item.run.fleet_run_id !== requestedRunId && item.run.target?.kind === "fleet_plan")
    .map((item) => ({
      runId: item.run.fleet_run_id,
      label: `${runTargetDisplay(item, t("history.repositoryCount", { count: item.run.targets.length }))} · ${t(outcomeKey(item.outcome))} · ${t(actionKey(item.run.capability.action))} · ${formatRunDate(item.run.accepted_at)}`,
    }));
  const comparisonAuthorityComplete = history.status === "read-fresh" && !history.hasMore;
  // Preserve a URL-bound baseline as a presentation option only while comparison-history
  // authority is incomplete. Once fresh pagination is exhausted, actual history alone
  // decides whether that baseline still exists.
  if (!comparisonAuthorityComplete && requestedReview.baselineRunId && !comparisonOptions.some((option) => option.runId === requestedReview.baselineRunId)) {
    comparisonOptions.push({ runId: requestedReview.baselineRunId, label: requestedReview.baselineRunId });
  }
  const canonicalReviewFacts = selectedDetail ? runReviewCanonicalFacts(selectedDetail, comparisonOptions, comparisonAuthorityComplete) : null;
  const review = canonicalReviewFacts ? normalizeRunReviewStateForLoadedRun(requestedReview, canonicalReviewFacts) : requestedReview;
  const locationHash = readLocationHash();
  const normalizedReviewHash = runReviewHash(review);
  useEffect(() => {
    if (locationHash !== normalizedReviewHash) writeRunReviewState("replace", review);
  }, [locationHash, normalizedReviewHash]);
  const error = history.status === "read-error"
    ? history.failure?.message ?? null
    : detail.resource.status === "read-error"
      ? detail.resource.failure?.message ?? null
      : fleet.resource.status === "read-error"
        ? fleet.resource.failure?.message ?? null
        : runStatus.activeRunError;
  const selectRun = (runId: string) => {
    writeRunReviewState(
      "push",
      patchRunReviewState(readRunReviewState(), { runId, view: "summary" }),
    );
  };
  const followStartedChildRun = async (
    parentRunId: string,
    command: () => Promise<WriteCommandResult<FleetRunState>>,
  ) => {
    const owner = presentationAuthority.capture();
    const result = await command();
    if (!presentationAuthority.owns(owner)) return;
    const childRunId = reviewChildRunId(
      result,
      parentRunId,
      readRunReviewState().runId,
    );
    if (childRunId) selectRun(childRunId);
  };
  const writeSelectedRunPatch = (
    mode: "replace" | "push",
    patch: SelectedRunPatch,
  ) => {
    if (!requestedRunId) return;
    writeRunReviewPatch(mode, { ...patch, runId: requestedRunId });
  };
  useEffect(() => {
    if (!selectedDetail || !review.failureFocus || review.stepExecutionId) return;
    const failedStep = selectedDetail.game_tape.issue_focus
      ?.find((item) => item.scope === "STEP" && item.step_execution_id)
      ?.step_execution_id ?? null;
    if (failedStep) {
      writeRunReviewPatch("replace", {
        runId: requestedRunId,
        stepExecutionId: failedStep,
        view: "flow",
        failureFocus: true,
      });
    }
  }, [selectedDetail, review.failureFocus, review.stepExecutionId]);
  useEffect(() => {
    if (!selectedDetail || !review.historyRepositoryId || review.stepExecutionId || review.failureFocus) return;
    const repository = selectedDetail.game_tape.repositories.find(
      (item) => item.repository.repository_id === review.historyRepositoryId,
    );
    const repositorySteps = repository?.steps ?? [];
    const step = repositorySteps.find((item) =>
      ["FAILED", "INTERRUPTED", "ATTENTION_REQUIRED", "RUNNING"].includes(item.status),
    ) ?? repositorySteps[0] ?? null;
    if (step) {
      writeRunReviewPatch("replace", {
        runId: requestedRunId,
        stepExecutionId: step.step_execution_id,
        view: "flow",
      });
    }
  }, [selectedDetail, review.failureFocus, review.historyRepositoryId, review.stepExecutionId]);
  const selectStep = (stepExecutionId: string | null) => {
    writeSelectedRunPatch("replace", {
      stepExecutionId,
      view: readRunReviewState().view ?? "flow",
      logPageFromEnd: 0,
    });
  };
  const selectView = (view: GameTapeView) => {
    writeSelectedRunPatch("push", { view });
  };
  const selectBaseline = (baselineRunId: string | null) => {
    writeSelectedRunPatch("push", {
      view: readRunReviewState().view ?? "diff",
      baselineRunId,
    });
  };
  const openComparison = (baselineRunId: string) => {
    writeSelectedRunPatch("push", { view: "diff", baselineRunId });
  };
  const selectHistoryQuery = (historyQuery: string) => {
    writeRunReviewPatch("replace", { historyQuery });
  };
  const selectHistoryFilter = (historyFilter: RunHistoryFilter) => {
    // History filters own only the list query. Explicit Run review remains URL-owned;
    // detail load timing must never decide whether the selected Run is discarded.
    writeRunReviewPatch("push", {
      historyFilter,
      historyOutcome: runHistoryFilterOutcome(historyFilter),
    });
  };
  const selectFailureFocus = (failureFocus: boolean) => {
    writeSelectedRunPatch("push", {
      view: readRunReviewState().view ?? "flow",
      failureFocus,
    });
  };
  const selectLogQuery = (logQuery: string) => {
    writeSelectedRunPatch("replace", {
      view: readRunReviewState().view ?? "logs",
      logQuery,
      logPageFromEnd: 0,
    });
  };
  const selectLogStream = (logStream: GameTapeLogStream) => {
    writeSelectedRunPatch("push", {
      view: readRunReviewState().view ?? "logs",
      logStream,
      logPageFromEnd: 0,
    });
  };
  const selectLogPageFromEnd = (logPageFromEnd: number) => {
    writeSelectedRunPatch("replace", {
      view: readRunReviewState().view ?? "logs",
      logPageFromEnd,
    });
  };
  const selectRawLogsOpen = (rawLogsOpen: boolean) => {
    writeSelectedRunPatch("push", {
      view: readRunReviewState().view ?? "logs",
      rawLogsOpen,
    });
  };
  const selectTechnicalOpen = (technicalOpen: boolean) => {
    writeSelectedRunPatch("push", {
      view: readRunReviewState().view ?? "flow",
      technicalOpen,
    });
  };
  const selectFlowQuery = (flowQuery: string) => {
    writeSelectedRunPatch("replace", {
      view: readRunReviewState().view ?? "flow",
      flowQuery,
    });
  };
  const selectCollapsedLaneIds = (
    collapsedLaneIds: readonly string[],
    historyMode: "replace" | "push",
  ) => {
    writeSelectedRunPatch(historyMode, {
      view: readRunReviewState().view ?? "flow",
      collapsedLaneIds,
    });
  };
  const selectSourceSegment = (
    sourceSegmentStepExecutionId: string | null,
    historyMode: "replace" | "push",
  ) => {
    writeSelectedRunPatch(historyMode, {
      view: readRunReviewState().view ?? "flow",
      sourceSegmentStepExecutionId,
    });
  };
  const copyStepLink = async (stepExecutionId: string): Promise<boolean> => {
    if (!requestedRunId) return false;
    const current = readRunReviewState();
    const state = patchRunReviewState(current, {
      runId: requestedRunId,
      stepExecutionId,
      view: current.view ?? "flow",
    });
    try {
      await navigator.clipboard.writeText(runReviewUrl(state, window.location.href));
      return true;
    } catch {
      return false;
    }
  };

  return <main className="page">
    <PageHeading eyebrow={t("runsPage.eyebrow")} title={t("runsPage.title")} description={t("runsPage.description")} action={<div className="button-row"><a className="button button--secondary" href={appLocationHash("runs", { campaign: "" })}>{t("gameTape.campaign.open")}</a><button className="button button--primary" type="button" onClick={onOpenSharedRuns}>{t("runsPage.newRun")}</button></div>} />
    {error ? <ErrorBanner message={error} /> : null}
    {requestedReview.historyOutcome || requestedReview.historyStatus || requestedReview.historySource || requestedReview.historyRepositoryId || requestedReview.historyFromAt || requestedReview.historyPlanQuery ? <div className="panel compact-card execution-filter-context" role="status">
      <div><strong>{t("runsPage.filteredTitle")}</strong><small>{requestedReview.historyRepositoryId ? t("runsPage.repositoryFilter", { repository: requestedReview.historyRepositoryId }) : requestedReview.historyPlanQuery ? requestedReview.historyPlanQuery : requestedReview.historyFromAt ? t("executionOverview.timeline.filteredPeriod") : requestedReview.historySource ? t(`history.sources.${requestedReview.historySource}`) : t(executionFilterKey(requestedReview.historyOutcome, requestedReview.historyStatus))}</small></div>
      <button className="text-button" type="button" onClick={() => writeRunReviewState("push", patchRunReviewState(readRunReviewState(), { historyOutcome: null, historyStatus: null, historySource: null, historyRepositoryId: null, historyFromAt: null, historyToAt: null, historyPlanQuery: "", historyFilter: "all" }))}>{t("runsPage.clearExecutionFilter")}</button>
    </div> : null}
    {history.status === "read-loading" && history.items.length === 0 ? <p className="muted loading-state" role="status">{t("history.loading")}</p> : null}
    {history.status !== "read-loading" && history.items.length === 0 ? <EmptyState
      title={t("runsPage.emptyTitle")}
      message={t("runsPage.emptyMessage")}
      action={<button className="button button--secondary" type="button" onClick={onOpenRepositories}>{t("runsPage.runRepository")}</button>}
    /> : null}
    {requestedRunId ? <RunReviewPicker
      items={history.items}
      selectedRunId={requestedRunId}
      onSelect={selectRun}
      onOpenHistory={() => document.getElementById("run-history")?.scrollIntoView({ behavior: "smooth", block: "start" })}
    /> : null}
    {selectedDetail ? <GameTape
      detail={selectedDetail}
      comparisonOptions={comparisonOptions}
      onOpenRunActions={() => document.getElementById("run-history")?.scrollIntoView({ behavior: "smooth", block: "start" })}
      onOpenRecovery={onOpenRecovery}
      stepExecutionId={review.stepExecutionId}
      view={review.view}
      baselineRunId={review.baselineRunId}
      failureFocus={review.failureFocus}
      logQuery={review.logQuery}
      logStream={review.logStream}
      logPageFromEnd={review.logPageFromEnd}
      rawLogsOpen={review.rawLogsOpen}
      technicalOpen={review.technicalOpen}
      flowQuery={review.flowQuery}
      collapsedLaneIds={review.collapsedLaneIds}
      sourceSegmentStepExecutionId={review.sourceSegmentStepExecutionId}
      onStepSelectionChange={selectStep}
      onViewChange={selectView}
      onBaselineChange={selectBaseline}
      onOpenComparison={openComparison}
      onFailureFocusChange={selectFailureFocus}
      onLogQueryChange={selectLogQuery}
      onLogStreamChange={selectLogStream}
      onLogPageChange={selectLogPageFromEnd}
      onRawLogsOpenChange={selectRawLogsOpen}
      onTechnicalOpenChange={selectTechnicalOpen}
      onFlowQueryChange={selectFlowQuery}
      onCollapsedLaneIdsChange={selectCollapsedLaneIds}
      onSourceSegmentChange={selectSourceSegment}
      onCopyStepLink={copyStepLink}
    /> : null}
    {history.items.length > 0 ? <RunHistory
      items={history.items}
      selected={selectedDetail}
      loading={history.status === "read-loading" || history.status === "read-refreshing"}
      detailLoading={detail.resource.status === "read-loading" || detail.resource.status === "read-refreshing"}
      hasMore={history.hasMore}
      summary={fleet.resource.data?.summary ?? null}
      actionsDisabled={runStatus.activeRun !== null || mutationBusy}
      query={review.historyQuery}
      filter={review.historyFilter}
      onQueryChange={selectHistoryQuery}
      onFilterChange={selectHistoryFilter}
      onSelect={selectRun}
      onLoadMore={() => void history.loadMore()}
      onClearSelection={() => writeRunReviewState(
        "replace",
        patchRunReviewState(readRunReviewState(), { runId: null }),
      )}
      onRunAgain={(runId) => void followStartedChildRun(runId, () => runActions.rerunFleet(runId))}
      onReadmit={(runId) => void followStartedChildRun(runId, () => runActions.readmitFleet(runId))}
      onOpenRecovery={onOpenRecovery}
    /> : null}
  </main>;
}

function runReviewCanonicalFacts(detail: FleetRunDetail, options: readonly GameTapeComparisonOption[], comparisonAuthorityComplete: boolean): RunReviewCanonicalFacts {
  const repositories = detail.game_tape.repositories ?? [];
  return {
    runId: detail.run.fleet_run_id,
    stepExecutionIds: new Set(repositories.flatMap((repository) => (repository.steps ?? []).map((step) => step.step_execution_id))),
    baselineRunIds: comparisonAuthorityComplete ? new Set(options.map((option) => option.runId)) : null,
    repositoryExecutionIds: new Set(repositories.map((repository) => repository.repository_execution_id)),
    sourceSegmentStepExecutionIds: new Set(repositories.flatMap((repository) => gameTapeFlowGroups(repository).flatMap((group) => group.steps[0]?.step_execution_id ? [group.steps[0].step_execution_id] : []))),
    hasIssues: (detail.game_tape.issue_count ?? 0) > 0,
  };
}


function executionFilterKey(outcome: string | null, status: string | null): string {
  if (outcome === "SUCCESS") return "executionOverview.states.successful";
  if (outcome === "FAILED") return "executionOverview.states.failed";
  if (outcome === "ATTENTION_REQUIRED") return "executionOverview.states.attentionRequired";
  if (outcome === "CANCELLED") return "executionOverview.states.cancelled";
  if (status === "RUNNING") return "executionOverview.states.running";
  if (status === "QUEUED") return "executionOverview.states.queued";
  if (status === "CANCELLING") return "executionOverview.states.cancelling";
  return "runsPage.title";
}
