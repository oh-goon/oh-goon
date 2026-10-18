import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { FleetRunDetail, GameTapeIssueFocus } from "@generated/models";
import { GameTapeAttempts } from "@/features/game-tape/components/GameTapeAttempts";
import { GameTapeAuthorityWhyNot } from "@/features/game-tape/components/GameTapeAuthorityWhyNot";
import { GameTapeDiff, type GameTapeComparisonOption } from "@/features/game-tape/components/GameTapeDiff";
import { GameTapeEvidence } from "@/features/game-tape/components/GameTapeEvidence";
import { GameTapeEnforcer } from "@/features/game-tape/components/GameTapeEnforcer";
import { GameTapeOutputs } from "@/features/game-tape/components/GameTapeOutputs";
import type { GameTapeLogStream } from "@/features/game-tape/components/GameTapeLiveLogs";
import { GameTapeStepInspector } from "@/features/game-tape/components/GameTapeStepInspector";
import { GameTapeFlow } from "@/features/game-tape/components/GameTapeFlow";
import { gameTapeFlowReviewFocus } from "@/features/game-tape/gameTapeFlowModel";
import { gameTapePreviousExactComparableRunId } from "@/features/game-tape/gameTapeFailureComparison";
import { GameTapePerformance } from "@/features/game-tape/components/GameTapePerformance";
import { GameTapePlayByPlay } from "@/features/game-tape/components/GameTapePlayByPlay";
import { GameTapeSearch } from "@/features/game-tape/components/GameTapeSearch";
import { GameTapeSummary } from "@/features/game-tape/components/GameTapeSummary";
import { GameTapeTimeline } from "@/features/game-tape/components/GameTapeTimeline";
import { GameTapeVerification } from "@/features/game-tape/components/GameTapeVerification";
import { statusTone } from "@/features/game-tape/gameTapeFormat";
import { findGameTapeStepContext, gameTapeFailureNavigation, gameTapeStepContexts } from "@/features/game-tape/gameTapeSteps";
import { useGameTapeComparison } from "@/features/game-tape/hooks/useGameTapeComparison";
import { useGameTapeFailureHistory } from "@/features/game-tape/hooks/useGameTapeFailureHistory";
import { useGameTapeLiveOutput } from "@/features/game-tape/hooks/useGameTapeLiveOutput";
import { useGameTapePerformance } from "@/features/game-tape/hooks/useGameTapePerformance";
import { useScoutRunEvidence } from "@/features/game-tape/hooks/useScoutRunEvidence";
import { gameTapeArtifactKey, gameTapeArtifacts } from "@/features/game-tape/gameTapeArtifacts";
import { gameTapeRecoveryAction } from "@/features/game-tape/runRecovery";
import { gameTapeExecutionReference } from "@/features/game-tape/gameTapeExecutionReference";

export type GameTapeView = "summary" | "flow" | "timeline" | "logs" | "outputs" | "verification" | "enforcer" | "performance" | "playByPlay" | "attempts" | "search" | "diff";
const GAME_TAPE_VIEWS = new Set<GameTapeView>(["summary", "flow", "timeline", "logs", "outputs", "verification", "enforcer", "performance", "playByPlay", "attempts", "search", "diff"]);
export function isGameTapeView(value: string | null): value is GameTapeView { return value !== null && GAME_TAPE_VIEWS.has(value as GameTapeView); }

interface GameTapeProps {
  detail: FleetRunDetail;
  compact?: boolean;
  comparisonOptions?: readonly GameTapeComparisonOption[];
  onOpenRunActions?: () => void;
  onOpenRecovery?: () => void;
  stepExecutionId?: string | null;
  view?: GameTapeView | null;
  baselineRunId?: string | null;
  failureFocus?: boolean;
  logQuery?: string;
  logStream?: GameTapeLogStream;
  logPageFromEnd?: number;
  rawLogsOpen?: boolean;
  technicalOpen?: boolean;
  flowQuery?: string;
  collapsedLaneIds?: readonly string[];
  sourceSegmentStepExecutionId?: string | null;
  onStepSelectionChange?: (stepExecutionId: string | null) => void;
  onViewChange?: (view: GameTapeView) => void;
  onBaselineChange?: (baselineRunId: string | null) => void;
  onOpenComparison?: (baselineRunId: string) => void;
  onFailureFocusChange?: (enabled: boolean) => void;
  onLogQueryChange?: (query: string) => void;
  onLogStreamChange?: (stream: GameTapeLogStream) => void;
  onLogPageChange?: (pageFromEnd: number) => void;
  onRawLogsOpenChange?: (open: boolean) => void;
  onTechnicalOpenChange?: (open: boolean) => void;
  onFlowQueryChange?: (query: string) => void;
  onCollapsedLaneIdsChange?: (repositoryExecutionIds: readonly string[], historyMode: "replace" | "push") => void;
  onSourceSegmentChange?: (firstStepExecutionId: string | null, historyMode: "replace" | "push") => void;
  onCopyStepLink?: (stepExecutionId: string) => Promise<boolean>;
}

export function GameTape(props: GameTapeProps) {
  return <GameTapeRun key={props.detail.game_tape.fleet_run_id} {...props} />;
}

function GameTapeRun({
  detail,
  compact = false,
  comparisonOptions = [],
  onOpenRunActions,
  onOpenRecovery,
  stepExecutionId = null,
  view: requestedView = null,
  baselineRunId: requestedBaselineRunId = null,
  failureFocus: requestedFailureFocus = false,
  logQuery = "",
  logStream = "all",
  logPageFromEnd = 0,
  rawLogsOpen = false,
  technicalOpen = false,
  flowQuery = "",
  collapsedLaneIds = [],
  sourceSegmentStepExecutionId = null,
  onStepSelectionChange,
  onViewChange,
  onBaselineChange,
  onOpenComparison,
  onFailureFocusChange,
  onLogQueryChange,
  onLogStreamChange,
  onLogPageChange,
  onRawLogsOpenChange,
  onTechnicalOpenChange,
  onFlowQueryChange,
  onCollapsedLaneIdsChange,
  onSourceSegmentChange,
  onCopyStepLink,
}: GameTapeProps) {
  const { t } = useTranslation();
  const tape = detail.game_tape;
  const hasComparison = comparisonOptions.length > 0;
  const viewAvailable = (candidate: GameTapeView) => !compact || !["performance", "playByPlay", "attempts", "search"].includes(candidate);
  const view = requestedView && viewAvailable(requestedView) && (requestedView !== "diff" || hasComparison) ? requestedView : "summary";
  const selectView = (next: GameTapeView) => { if (!viewAvailable(next) || (next === "diff" && !hasComparison)) return; onViewChange?.(next); };
  const validBaseline = comparisonOptions.some((option) => option.runId === requestedBaselineRunId) ? requestedBaselineRunId : null;
  const selectBaseline = (next: string | null) => {
    const accepted = comparisonOptions.some((option) => option.runId === next) ? next : null;
    onBaselineChange?.(accepted);
  };
  const comparison = useGameTapeComparison(tape.fleet_run_id, validBaseline);
  const performance = useGameTapePerformance(tape.fleet_run_id, view === "performance" || view === "diff");
  const scoutRun = useScoutRunEvidence(tape.fleet_run_id, view === "performance");
  const liveOutputEnabled = view === "logs" || view === "search";
  const liveOutput = useGameTapeLiveOutput(
    tape.fleet_run_id,
    liveOutputEnabled,
    !tape.receipt.finished_at,
  );
  const stepContexts = useMemo(() => gameTapeStepContexts(tape), [tape]);
  const selectedId = stepExecutionId && stepContexts.some((context) => context.step.step_execution_id === stepExecutionId) ? stepExecutionId : null;
  const reviewTriggerRef = useRef<HTMLElement | null>(null);
  const hasIssues = tape.issue_count !== undefined && tape.issue_count > 0;
  const failureFocus = requestedFailureFocus && hasIssues;
  const selectStep = (nextStepExecutionId: string | null) => {
    if (nextStepExecutionId) {
      const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      if (active && !active.closest(".game-tape-inspector")) reviewTriggerRef.current = active;
    }
    onStepSelectionChange?.(nextStepExecutionId);
    if (nextStepExecutionId === null) requestAnimationFrame(() => { const trigger = reviewTriggerRef.current; if (trigger?.isConnected) trigger.focus(); });
  };
  const selectedContext = findGameTapeStepContext(stepContexts, selectedId);
  const firstReviewFocus = useMemo(() => tape.repositories.map(gameTapeFlowReviewFocus).find((step) => step !== null) ?? null, [tape.repositories]);
  const selected = selectedContext?.step ?? null;
  const failureNavigation = gameTapeFailureNavigation(stepContexts, selectedId);
  const previousFailureId = failureNavigation.previous?.step.step_execution_id ?? null;
  const nextFailureId = failureNavigation.next?.step.step_execution_id ?? null;
  useEffect(() => {
    if (!selected) return;
    const closeInspector = (event: KeyboardEvent) => { if (event.key === "Escape") selectStep(null); };
    window.addEventListener("keydown", closeInspector);
    return () => window.removeEventListener("keydown", closeInspector);
  }, [selected]);
  const failureHistory = useGameTapeFailureHistory(
    tape.fleet_run_id,
    selectedContext?.repository.repository.repository_id ?? null,
    selected?.step_id ?? null,
    selected?.failure !== null && selected?.failure !== undefined,
  );
  const previousComparableRunId = comparisonOptions.some(
    (option) => option.runId === performance.data?.previous_comparable_run_id,
  ) ? performance.data?.previous_comparable_run_id ?? null : null;
  const previousExactComparableRunId = gameTapePreviousExactComparableRunId(failureHistory.data, comparisonOptions);
  const openPreviousExactComparison = previousExactComparableRunId ? () => {
    if (onOpenComparison) onOpenComparison(previousExactComparableRunId);
    else { onBaselineChange?.(previousExactComparableRunId); onViewChange?.("diff"); }
  } : undefined;
  const primaryViews: readonly GameTapeView[] = ["summary", "timeline", "flow", "logs", "outputs", "verification", "enforcer"];
  const advancedViews: readonly GameTapeView[] = comparisonOptions.length > 0
    ? ["diff", "performance", "playByPlay", "attempts", "search"]
    : ["performance", "playByPlay", "attempts", "search"];
  const artifacts = useMemo(() => gameTapeArtifacts(tape), [tape]);
  const selectedArtifacts = selectedContext ? artifacts.get(gameTapeArtifactKey(selectedContext.repository.repository.repository_id, selectedContext.step.step_id)) ?? [] : [];
  const issueCount = tape.issue_count ?? 0;
  const hiddenIssueCount = tape.hidden_issue_count ?? 0;

  if (!gameTapeExecutionReference(tape)) return null;

  const viewClasses = [
    "panel",
    "game-tape-view",
    compact ? "game-tape-view--compact" : null,
    selected ? "game-tape-view--step-selected" : null,
  ].filter(Boolean).join(" ");

  return <section className={viewClasses} aria-label={t("gameTape.title")}>
    <header className="game-tape-view__header">
      <div className="game-tape-view__heading-copy">
        <span className="eyebrow">{t("gameTape.eyebrow")}</span>
        <h2>{t("gameTape.title")}</h2>
        <p>{t("gameTape.description")}</p>
        <p className={`game-tape-state-guidance game-tape-state-guidance--${String(tape.status).toLowerCase()}`} role="status" aria-live="polite">{t(`gameTape.state.${tape.status}`)}</p>
      </div>
      <div className="game-tape-view__status" role="status" aria-live="polite"><StatusBadge label={t(`gameTape.status.${tape.status}`)} tone={statusTone(tape.status)} /></div>
    </header>
    <GameTapeRecovery
      detail={detail}
      onOpenRunActions={onOpenRunActions}
      onOpenRecovery={onOpenRecovery}
    />
    <GameTapeAuthorityWhyNot tape={tape} />
    {hasIssues ? <GameTapeIssueOverview
      issues={tape.issue_focus ?? []}
      issueCount={issueCount}
      hiddenCount={hiddenIssueCount}
      failureFocus={failureFocus}
      onToggleFailureFocus={() => {
        const next = !failureFocus;
        onFailureFocusChange?.(next);
        if (next && !selectedId && firstReviewFocus) selectStep(firstReviewFocus.step_execution_id);
      }}
      onInspectStep={(stepExecutionId) => {
        selectStep(stepExecutionId);
      }}
    /> : null}
    <nav className="game-tape-tabs game-tape-tabs--primary" aria-label={t("gameTape.primaryViews")}>
      {primaryViews.map((item) => <ViewButton key={item} item={item} selected={view === item} onSelect={selectView} />)}
    </nav>
    {!compact ? <details className="advanced-disclosure game-tape-more-views"><summary>{t("gameTape.moreViews")}</summary>
      <p className="game-tape-more-views__contract"><strong>{t("gameTape.contractTitle")}</strong> {t("gameTape.contract")}</p>
      <nav className="game-tape-tabs" aria-label={t("gameTape.advancedViews")}>
        {advancedViews.map((item) => <ViewButton key={item} item={item} selected={view === item} onSelect={selectView} />)}
      </nav>
    </details> : null}
    <div className={selected ? "game-tape-review-workspace game-tape-review-workspace--inspecting" : "game-tape-review-workspace"}>
      <div className="game-tape-review-surface">
        {view === "summary" ? <GameTapeSummary tape={tape} compact={compact} /> : null}
        {view === "flow" ? <GameTapeFlow tape={tape} selectedId={selectedId} onSelect={selectStep} onCopyStepLink={onCopyStepLink} failureFocus={failureFocus} query={flowQuery} onQueryChange={onFlowQueryChange} collapsedLaneIds={collapsedLaneIds} onCollapsedLaneIdsChange={onCollapsedLaneIdsChange} sourceSegmentStepExecutionId={sourceSegmentStepExecutionId} onSourceSegmentChange={onSourceSegmentChange} /> : null}
        {view === "timeline" ? <GameTapeTimeline tape={tape} selectedId={selected?.step_execution_id ?? null} onSelect={selectStep} failureFocus={failureFocus} /> : null}
        {view === "performance" ? <GameTapePerformance report={performance.data} loading={performance.status === "read-loading" || performance.status === "read-refreshing"} scout={scoutRun.data} scoutLoading={scoutRun.status === "read-loading" || scoutRun.status === "read-refreshing"} onSelect={selectStep} /> : null}
        {view === "playByPlay" ? <GameTapePlayByPlay tape={tape} onSelect={selectStep} /> : null}
        {view === "attempts" ? <GameTapeAttempts tape={tape} selectedId={selected?.step_execution_id ?? null} onSelect={selectStep} /> : null}
        {view === "outputs" ? <GameTapeOutputs tape={tape} /> : null}
        {view === "verification" ? <GameTapeVerification tape={tape} /> : null}
        {view === "enforcer" ? <GameTapeEnforcer tape={tape} /> : null}
        {view === "logs" ? <GameTapeEvidence tape={tape} selectedId={selected?.step_execution_id ?? null} onSelect={selectStep} failureHistory={failureHistory.data} failureHistoryLoading={failureHistory.status === "read-loading"} liveOutput={liveOutput.data} artifacts={artifacts} logQuery={logQuery} logStream={logStream} logPageFromEnd={logPageFromEnd} onLogQueryChange={onLogQueryChange} onLogStreamChange={onLogStreamChange} onLogPageChange={onLogPageChange} rawLogsOpen={rawLogsOpen} onRawLogsOpenChange={onRawLogsOpenChange} /> : null}
        {view === "search" ? <GameTapeSearch tape={tape} onSelect={selectStep} liveOutput={liveOutput.data} /> : null}
        {view === "diff" ? <GameTapeDiff diff={comparison.data} options={comparisonOptions} baselineRunId={validBaseline} previousComparableRunId={previousComparableRunId} loading={comparison.status === "read-loading" || comparison.status === "read-refreshing"} onBaselineChange={selectBaseline} /> : null}
      </div>
      {selected && ["flow", "timeline", "performance", "playByPlay", "logs", "search"].includes(view) ? <GameTapeStepInspector key={selected.step_execution_id} step={selected} artifacts={selectedArtifacts} failureHistory={failureHistory.data} failureHistoryLoading={failureHistory.status === "read-loading"} technicalOpen={technicalOpen} onTechnicalOpenChange={onTechnicalOpenChange} onClose={() => selectStep(null)} onOpenEvidence={() => selectView("logs")} onPreviousFailure={previousFailureId ? () => selectStep(previousFailureId) : undefined} onNextFailure={nextFailureId ? () => selectStep(nextFailureId) : undefined} onComparePreviousOccurrence={openPreviousExactComparison} /> : null}
    </div>
  </section>;
}

function ViewButton({ item, selected, onSelect }: { item: GameTapeView; selected: boolean; onSelect(view: GameTapeView): void }) {
  const { t } = useTranslation();
  return <button className={selected ? "game-tape-tab game-tape-tab--active" : "game-tape-tab"} type="button" aria-pressed={selected} onClick={() => onSelect(item)}>{t(`gameTape.${item}.title`)}</button>;
}



function GameTapeIssueOverview({
  issues,
  issueCount,
  hiddenCount,
  failureFocus,
  onToggleFailureFocus,
  onInspectStep,
}: {
  issues: readonly GameTapeIssueFocus[];
  issueCount: number;
  hiddenCount: number;
  failureFocus: boolean;
  onToggleFailureFocus(): void;
  onInspectStep(stepExecutionId: string): void;
}) {
  const { t } = useTranslation();
  return <section className="game-tape-issue-overview" aria-label={t("gameTape.issues.title")} role="alert" aria-live="polite" aria-atomic="true">
    <header>
      <div><span className="eyebrow">{t("gameTape.issues.eyebrow")}</span><strong>{t("gameTape.issues.title")}</strong></div>
      <div className="button-row"><span>{t("gameTape.issues.count", { count: issueCount })}</span><button className="button button--secondary button--compact" type="button" aria-pressed={failureFocus} onClick={onToggleFailureFocus}>{t(failureFocus ? "gameTape.issues.showAll" : "gameTape.issues.focusFailures")}</button></div>
    </header>
    {failureFocus ? <small>{t("gameTape.issues.focusFailuresHelp")}</small> : null}
    <div className="game-tape-issue-overview__items">
      {issues.map((issue, index) => {
        const stepExecutionId = issue.step_execution_id;
        const key = `${issue.scope}:${issue.repository_execution_id ?? "run"}:${stepExecutionId ?? index}`;
        const content = <>
          <span>{issue.repository_name ?? t("gameTape.issues.run")}</span>
          <strong>{issue.step_id ?? issue.code ?? t("gameTape.issues.repositoryFailure")}</strong>
          <small>{[
            issue.code,
            issue.exit_code !== null && issue.exit_code !== undefined
              ? t("gameTape.issues.exitCode", { code: issue.exit_code })
              : null,
          ].filter(Boolean).join(" · ") || issue.message || t("gameTape.issues.noCode")}</small>
          {issue.message && (issue.code || issue.exit_code !== null && issue.exit_code !== undefined)
            ? <span className="game-tape-issue-overview__message">{issue.message}</span>
            : null}
          {issue.step_execution_id ? <em>{t("gameTape.issues.openEvidence")}</em> : null}
        </>;
        return stepExecutionId
          ? <button
              className="game-tape-issue-overview__item"
              type="button"
              key={key}
              onClick={() => onInspectStep(stepExecutionId)}
            >{content}</button>
          : <div className="game-tape-issue-overview__item" key={key}>{content}</div>;
      })}
    </div>
    {hiddenCount > 0 ? <small>{t("gameTape.issues.more", { count: hiddenCount })}</small> : null}
    <small>{t("gameTape.issues.contract")}</small>
  </section>;
}
function GameTapeRecovery({
  detail,
  onOpenRunActions,
  onOpenRecovery,
}: {
  detail: FleetRunDetail;
  onOpenRunActions?: () => void;
  onOpenRecovery?: () => void;
}) {
  const { t } = useTranslation();
  const action = gameTapeRecoveryAction(detail.run, detail.rerun_supported);
  if (action === "none" || detail.run.status !== "FAILED") return null;
  const failure = detail.run.failure;
  const handler = action === "open-recovery" ? onOpenRecovery : onOpenRunActions;
  const actionKey = action === "open-recovery" ? "openRecovery" : "openRunActions";
  return <section className="game-tape-recovery" aria-label={t("gameTape.recovery.title")}>
    <div>
      <span className="eyebrow">{t("gameTape.recovery.eyebrow")}</span>
      <strong>{t(`gameTape.recovery.${action}.title`)}</strong>
      <p>{t(`gameTape.recovery.${action}.description`)}</p>
      <small>{failure.code} · {failure.message}</small>
    </div>
    {handler ? <button className="button button--secondary" type="button" onClick={handler}>
      {t(`gameTape.recovery.${actionKey}`)}
    </button> : null}
  </section>;
}
