import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { GameTapeStatus as GameTapeStatusValue } from "@generated/enums";
import type { GameTape, GameTapeRepositoryExecution, GameTapeStepExecution } from "@generated/models";
import { duration, statusSymbol, statusTone } from "@/features/game-tape/gameTapeFormat";
import {
  gameTapeFlowGroups,
  gameTapeFlowRepositoryStatusCounts,
  gameTapeFlowReviewFocus,
  gameTapeFlowSearchMatches,
  gameTapeFlowSegmentAnchor,
  gameTapeFlowSelectionContext,
  gameTapeFlowStatusCounts,
  gameTapeFlowStatusMatches,
  type GameTapeFlowGroup,
} from "@/features/game-tape/gameTapeFlowModel";

const GAME_TAPE_FLOW_REPOSITORY_PAGE_SIZE = 40;

export function GameTapeFlow({ tape, selectedId, onSelect, onCopyStepLink, failureFocus = false, query = "", onQueryChange, collapsedLaneIds: requestedCollapsedLaneIds = [], onCollapsedLaneIdsChange, sourceSegmentStepExecutionId = null, onSourceSegmentChange }: { tape: GameTape; selectedId: string | null; onSelect(id: string): void; onCopyStepLink?(stepExecutionId: string): Promise<boolean>; failureFocus?: boolean; query?: string; onQueryChange?: (query: string) => void; collapsedLaneIds?: readonly string[]; onCollapsedLaneIdsChange?: (repositoryExecutionIds: readonly string[], historyMode: "replace" | "push") => void; sourceSegmentStepExecutionId?: string | null; onSourceSegmentChange?: (firstStepExecutionId: string | null, historyMode: "replace" | "push") => void }) {
  const { t } = useTranslation();
  // Query/folding/source orientation are durable controlled review state. Everything
  // below is ephemeral to this mounted Run and is reset by GameTape's Run key.
  const [activeMatchIndex, setActiveMatchIndex] = useState(-1);
  useEffect(() => { setActiveMatchIndex(-1); }, [query]);
  const laneIds = useMemo(() => tape.repositories.map((repository) => repository.repository_execution_id), [tape.repositories]);
  const [repositoryPage, setRepositoryPage] = useState(0);
  const collapsedLaneIds = useMemo(() => new Set(laneIds.filter((repositoryExecutionId) => requestedCollapsedLaneIds.includes(repositoryExecutionId))), [laneIds, requestedCollapsedLaneIds]);
  const sourceSegmentAnchor = useMemo(() => gameTapeFlowSegmentAnchor(tape.repositories, sourceSegmentStepExecutionId), [sourceSegmentStepExecutionId, tape.repositories]);
  const commitCollapsedLaneIds = (next: Set<string>, historyMode: "replace" | "push" = "push") => {
    onCollapsedLaneIdsChange?.(laneIds.filter((repositoryExecutionId) => next.has(repositoryExecutionId)), historyMode);
    if (historyMode === "push" && sourceSegmentAnchor && next.has(sourceSegmentAnchor.repositoryExecutionId)) onSourceSegmentChange?.(null, "replace");
  };
  const [copiedSource, setCopiedSource] = useState<string | null>(null);
  const [copiedStepLinkId, setCopiedStepLinkId] = useState<string | null>(null);
  const copyOwnerRef = useRef("");
  const matches = useMemo(() => gameTapeFlowSearchMatches(tape.repositories, query), [query, tape.repositories]);
  const matchStepIds = useMemo(() => new Set<string>(matches.map((match) => match.stepExecutionId)), [matches]);
  const matchRepositoryIds = useMemo(() => new Set<string>(matches.map((match) => match.repositoryExecutionId)), [matches]);
  const statusCounts = useMemo(() => gameTapeFlowRepositoryStatusCounts(tape.repositories), [tape.repositories]);
  const totalSourceSegments = useMemo(() => tape.repositories.reduce((total, repository) => total + gameTapeFlowGroups(repository).length, 0), [tape.repositories]);
  const selection = useMemo(() => gameTapeFlowSelectionContext(tape.repositories, selectedId), [selectedId, tape.repositories]);
  copyOwnerRef.current = canonicalIdentity({
    runId: tape.fleet_run_id,
    selectedId,
    selectedStepExecutionId: selection?.step.step_execution_id ?? null,
    selectedSource: selection?.group.source ?? null,
  });
  const currentSourceGroupKey = selection?.group.key ?? sourceSegmentAnchor?.groupKey ?? null;
  const selectedRepositoryExecutionId = tape.repositories.find((repository) => (repository.steps ?? []).some((step) => step.step_execution_id === selectedId))?.repository_execution_id ?? null;
  const repositoryPageCount = Math.max(1, Math.ceil(tape.repositories.length / GAME_TAPE_FLOW_REPOSITORY_PAGE_SIZE));
  const boundedRepositoryPage = Math.min(repositoryPage, repositoryPageCount - 1);
  useEffect(() => { if (repositoryPage !== boundedRepositoryPage) setRepositoryPage(boundedRepositoryPage); }, [boundedRepositoryPage, repositoryPage]);
  const repositoryPageStart = boundedRepositoryPage * GAME_TAPE_FLOW_REPOSITORY_PAGE_SIZE;
  const pageRepositories = tape.repositories.slice(repositoryPageStart, repositoryPageStart + GAME_TAPE_FLOW_REPOSITORY_PAGE_SIZE);
  const pageLaneIds = pageRepositories.map((repository) => repository.repository_execution_id);
  const pagePassedLaneIds = pageRepositories.filter((repository) => repository.status === GameTapeStatusValue.PASSED).map((repository) => repository.repository_execution_id);
  const showRepository = (repositoryExecutionId: string) => {
    const index = tape.repositories.findIndex((repository) => repository.repository_execution_id === repositoryExecutionId);
    if (index >= 0) setRepositoryPage(Math.floor(index / GAME_TAPE_FLOW_REPOSITORY_PAGE_SIZE));
  };
  useEffect(() => { if (selectedRepositoryExecutionId) showRepository(selectedRepositoryExecutionId); }, [selectedRepositoryExecutionId]);
  const expandLaneForStep = (stepExecutionId: string) => {
    const repositoryExecutionId = tape.repositories.find((repository) => (repository.steps ?? []).some((step) => step.step_execution_id === stepExecutionId))?.repository_execution_id;
    if (!repositoryExecutionId) return;
    if (!collapsedLaneIds.has(repositoryExecutionId)) return;
    const next = new Set<string>(collapsedLaneIds);
    next.delete(repositoryExecutionId);
    commitCollapsedLaneIds(next, "replace");
  };
  const revealSourceSegment = (firstStepExecutionId: string) => {
    const anchor = gameTapeFlowSegmentAnchor(tape.repositories, firstStepExecutionId);
    if (!anchor) return false;
    showRepository(anchor.repositoryExecutionId);
    if (collapsedLaneIds.has(anchor.repositoryExecutionId)) {
      const next = new Set<string>(collapsedLaneIds);
      next.delete(anchor.repositoryExecutionId);
      commitCollapsedLaneIds(next, "replace");
    }
    requestAnimationFrame(() => document.getElementById(sourceGroupDomId(anchor.groupKey))?.scrollIntoView({ behavior: preferredScrollBehavior(), block: "nearest", inline: "center" }));
    return true;
  };
  const collapsedLaneKey = JSON.stringify([...collapsedLaneIds]);
  useEffect(() => {
    if (sourceSegmentStepExecutionId === null) return;
    if (!sourceSegmentAnchor) return;
    revealSourceSegment(sourceSegmentAnchor.firstStepExecutionId);
  }, [sourceSegmentStepExecutionId, sourceSegmentAnchor?.groupKey, collapsedLaneKey]);
  const selectSourceSegment = (firstStepExecutionId: string) => {
    if (!revealSourceSegment(firstStepExecutionId)) return;
    onSourceSegmentChange?.(firstStepExecutionId, "push");
  };
  const scrollToStep = (stepExecutionId: string) => {
    const repository = tape.repositories.find((candidate) => (candidate.steps ?? []).some((step) => step.step_execution_id === stepExecutionId));
    if (repository) showRepository(repository.repository_execution_id);
    expandLaneForStep(stepExecutionId);
    requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(() => document.getElementById(stepDomId(stepExecutionId))?.scrollIntoView({ behavior: preferredScrollBehavior(), block: "nearest", inline: "center" }))));
  };
  const focusStep = (stepExecutionId: string) => {
    onSelect(stepExecutionId);
    scrollToStep(stepExecutionId);
  };
  const focusLane = (repositoryExecutionId: string) => {
    const repository = tape.repositories.find((candidate) => candidate.repository_execution_id === repositoryExecutionId);
    if (!repository) return;
    const selectedStepInLane = (repository.steps ?? []).find((step) => step.step_execution_id === selectedId);
    const targetStepExecutionId = selectedStepInLane?.step_execution_id ?? repository.steps?.[0]?.step_execution_id ?? null;
    if (targetStepExecutionId) {
      focusStep(targetStepExecutionId);
      return;
    }
    document.getElementById(laneDomId(repositoryExecutionId))?.scrollIntoView({ behavior: preferredScrollBehavior(), block: "nearest" });
  };
  const moveMatch = (direction: -1 | 1) => {
    if (matches.length === 0) return;
    const nextIndex = activeMatchIndex < 0
      ? direction > 0 ? 0 : matches.length - 1
      : (activeMatchIndex + direction + matches.length) % matches.length;
    setActiveMatchIndex(nextIndex);
    const match = matches[nextIndex];
    if (match) focusStep(match.stepExecutionId);
  };
  const updateQuery = (value: string) => {
    setActiveMatchIndex(-1);
    onQueryChange?.(value);
  };
  const jumpStatus = (status: GameTapeStepExecution["status"]) => {
    const statusMatches = gameTapeFlowStatusMatches(tape.repositories, status);
    if (statusMatches.length === 0) return;
    const selectedIndex = statusMatches.findIndex((match) => match.stepExecutionId === selectedId);
    const nextIndex = selectedIndex < 0 ? 0 : (selectedIndex + 1) % statusMatches.length;
    const match = statusMatches[nextIndex];
    if (match) focusStep(match.stepExecutionId);
  };

  return <section className="game-tape-flowchart" data-failure-focus={failureFocus ? "true" : undefined} aria-label={t("gameTape.flow.ariaLabel")}>
    <header className="game-tape-flowchart__heading">
      <div>
        <span className="eyebrow">{t("gameTape.flow.eyebrow")}</span>
        <h3>{t("gameTape.flow.heading")}</h3>
        <p>{t("gameTape.flow.description")}</p>
      </div>
      <div className="game-tape-flowchart__legend" role="group" aria-label={t("gameTape.flow.legendLabel")}>
        <span>{t("gameTape.flow.plan")}</span><i aria-hidden="true">→</i><span>{t("gameTape.flow.goon")}</span><i aria-hidden="true">→</i><span>{t("gameTape.flow.step")}</span><i aria-hidden="true">→</i><span>{t("gameTape.flow.outcome")}</span>
      </div>
    </header>

    <article className="game-tape-flowchart__plan" data-status={tape.status}>
      <div>
        <span className="game-tape-flowchart__type">{t("gameTape.flow.plan")}</span>
        <strong>{tape.execution_reference?.plan_name ?? tape.execution_reference?.plan_id}</strong>
        <small>{t("gameTape.flow.planMetaDetailed", { repositories: tape.summary?.repositories ?? tape.repositories.length, steps: tape.summary?.planned_steps ?? tape.repositories.reduce((total, repository) => total + (repository.steps ?? []).length, 0), segments: totalSourceSegments })}</small>
        {tape.receipt?.duration_ms !== undefined ? <small>{t("gameTape.flow.recordedDuration", { duration: duration(tape.receipt.duration_ms) })}</small> : null}
      </div>
      <StatusBadge label={t(`gameTape.status.${tape.status}`)} tone={statusTone(tape.status)} />
    </article>

    {statusCounts.length > 0 ? <nav className="game-tape-flowchart__score" aria-label={t("gameTape.flow.statusNavigation")}>
      {statusCounts.map(({ status, count }) => <button className="game-tape-flowchart__lane-link" key={status} type="button" onClick={() => jumpStatus(status)} aria-label={t("gameTape.flow.jumpStatus", { count, status: t(`gameTape.status.${status}`) })}><StatusBadge label={t("gameTape.flow.statusCount", { count, status: t(`gameTape.status.${status}`) })} tone={statusTone(status)} /></button>)}
    </nav> : null}

    <div className="form-grid game-tape-flowchart__find" role="search" aria-label={t("gameTape.flow.findRegion")}>
      <label><span>{t("gameTape.flow.findLabel")}</span><input type="search" value={query} placeholder={t("gameTape.flow.findPlaceholder")} onChange={(event) => updateQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); moveMatch(event.shiftKey ? -1 : 1); } }} /></label>
      <span className="muted" role="status" aria-live="polite">{query.trim() ? t("gameTape.flow.matchCount", { count: matches.length }) : t("gameTape.flow.findHelp")}</span>
      <div className="button-row"><button className="button button--secondary button--compact" type="button" disabled={matches.length === 0} onClick={() => moveMatch(-1)}>{t("gameTape.flow.previousMatch")}</button><button className="button button--secondary button--compact" type="button" disabled={matches.length === 0} onClick={() => moveMatch(1)}>{t("gameTape.flow.nextMatch")}</button>{query ? <button className="text-button" type="button" onClick={() => updateQuery("")}>{t("common.clearFilters")}</button> : null}</div>
    </div>

    {selection ? <aside className="compact-card game-tape-flowchart__selected-dock" role="status" aria-live="polite" aria-label={t("gameTape.flow.selectedContext")}>
      <div className="game-tape-flowchart__selected-copy">
        <span className="eyebrow">{t("gameTape.flow.selectedStep")}</span>
        <strong>{selection.repository.repository.name} → {selection.group.kind === "goon" ? selection.group.displayName ?? t("gameTape.flow.repositoryGoon") : selection.group.kind === "source" ? selection.group.displayName ?? t("gameTape.flow.otherSource") : t("gameTape.flow.resolvedSteps")} → {selection.step.step_id}</strong>
        <small><strong>{t("gameTape.flow.recordedSourceLabel")}</strong> <code>{selection.group.source ?? t("gameTape.flow.noRecordedSource")}</code>{selection.group.source ? <button type="button" className="game-tape-copy-command" onClick={() => { const source = selection.group.source; if (!source) return; const owner = canonicalIdentity({ parent: copyOwnerRef.current, stepExecutionId: selection.step.step_execution_id, source }); void navigator.clipboard.writeText(source).then(() => { if (canonicalIdentity({ parent: copyOwnerRef.current, stepExecutionId: selection.step.step_execution_id, source }) === owner) setCopiedSource(source); }, () => { if (canonicalIdentity({ parent: copyOwnerRef.current, stepExecutionId: selection.step.step_execution_id, source }) === owner) setCopiedSource(null); }); }}>{copiedSource === selection.group.source ? t("gameTape.flow.sourceCopied") : t("gameTape.flow.copySource")}</button> : null}</small>
      </div>
      <dl className="game-tape-flowchart__selected-metrics">
        <div><dt>{t("gameTape.flow.repositoryMetric")}</dt><dd>{t("gameTape.flow.repositoryPosition", { current: selection.repositoryIndex + 1, total: selection.repositoryCount })}</dd></div>
        <div><dt>{t("gameTape.flow.segmentMetric")}</dt><dd>{t("gameTape.flow.segmentPosition", { current: selection.segmentIndex + 1, total: selection.segmentCount })}</dd></div>
        <div><dt>{t("gameTape.flow.stepMetric")}</dt><dd>{t("gameTape.flow.lanePosition", { current: selection.laneStepIndex + 1, total: selection.laneStepCount })}</dd></div>
      </dl>
      <div className="button-row game-tape-flowchart__selected-actions">
        <StatusBadge label={t(`gameTape.status.${selection.step.status}`)} tone={statusTone(selection.step.status)} />
        <button className="button button--secondary button--compact" type="button" disabled={selection.previousStepExecutionId === null} onClick={() => { if (selection.previousStepExecutionId) focusStep(selection.previousStepExecutionId); }}>{t("gameTape.flow.previousStep")}</button>
        <button className="button button--secondary button--compact" type="button" disabled={selection.nextStepExecutionId === null} onClick={() => { if (selection.nextStepExecutionId) focusStep(selection.nextStepExecutionId); }}>{t("gameTape.flow.nextStep")}</button>
        <button className="button button--secondary button--compact" type="button" disabled={selection.previousSegmentFirstStepExecutionId === null} onClick={() => { if (selection.previousSegmentFirstStepExecutionId) focusStep(selection.previousSegmentFirstStepExecutionId); }}>{t("gameTape.flow.previousSegment")}</button>
        <button className="button button--secondary button--compact" type="button" disabled={selection.nextSegmentFirstStepExecutionId === null} onClick={() => { if (selection.nextSegmentFirstStepExecutionId) focusStep(selection.nextSegmentFirstStepExecutionId); }}>{t("gameTape.flow.nextSegment")}</button>
        {onCopyStepLink ? <button className="text-button" type="button" onClick={() => { const stepExecutionId = selection.step.step_execution_id; const owner = canonicalIdentity({ parent: copyOwnerRef.current, stepExecutionId }); void onCopyStepLink(stepExecutionId).then((copied) => { if (canonicalIdentity({ parent: copyOwnerRef.current, stepExecutionId }) === owner) setCopiedStepLinkId(copied ? stepExecutionId : null); }); }}>{copiedStepLinkId === selection.step.step_execution_id ? t("gameTape.flow.stepLinkCopied") : t("gameTape.flow.copyStepLink")}</button> : null}
        <button className="text-button" type="button" onClick={() => scrollToStep(selection.step.step_execution_id)}>{t("gameTape.flow.jumpToSelected")}</button>
      </div>
    </aside> : null}

    <div className="game-tape-flowchart__branch" aria-hidden="true" />
    <div className="compact-card" role="group" aria-label={t("gameTape.flow.repositoryScopeLabel")}>
      <div><span className="eyebrow">{t("gameTape.flow.repositoryScope")}</span><strong>{t("gameTape.flow.repositoryScopeCount", { count: tape.repositories.length })}</strong><small>{t("gameTape.flow.repositoryScopeTruth")}</small></div>
      <span className="game-tape-flowchart__type">{t("gameTape.flow.repositoryScopeOrder")}</span>
    </div>
    <RepositoryScopeConnector laneCount={pageRepositories.length} />
    {tape.repositories.length > GAME_TAPE_FLOW_REPOSITORY_PAGE_SIZE ? <RepositoryPager page={boundedRepositoryPage} pageCount={repositoryPageCount} from={repositoryPageStart + 1} to={Math.min(repositoryPageStart + pageRepositories.length, tape.repositories.length)} total={tape.repositories.length} onPage={setRepositoryPage} /> : null}
    {tape.repositories.length > 1 ? <nav className="game-tape-flowchart__lane-nav" aria-label={t("gameTape.flow.laneNavigation")}>
      {pageRepositories.map((repository, localRepositoryIndex) => <button
        className={["game-tape-flowchart__lane-link", selectedRepositoryExecutionId === repository.repository_execution_id ? "game-tape-flowchart__lane-link--selected" : "", matchRepositoryIds.has(repository.repository_execution_id) ? "game-tape-flowchart__lane-link--match" : ""].filter(Boolean).join(" ")}
        type="button"
        key={repository.repository_execution_id}
        aria-current={selectedRepositoryExecutionId === repository.repository_execution_id ? "true" : undefined}
        onClick={() => focusLane(repository.repository_execution_id)}
      >
        <span>{t("gameTape.flow.laneLink", { current: repositoryPageStart + localRepositoryIndex + 1, repository: repository.repository.name })}</span><StatusBadge label={t(`gameTape.status.${repository.status}`)} tone={statusTone(repository.status)} />
      </button>)}
    </nav> : null}
    {tape.repositories.length > 1 ? <div className="button-row" role="group" aria-label={t("gameTape.flow.laneDisclosureControls")}>
      <button className="button button--secondary button--compact" type="button" disabled={pagePassedLaneIds.length === 0 || pagePassedLaneIds.every((id) => collapsedLaneIds.has(id))} onClick={() => commitCollapsedLaneIds(new Set<string>([...collapsedLaneIds, ...pagePassedLaneIds]))}>{t(tape.repositories.length > GAME_TAPE_FLOW_REPOSITORY_PAGE_SIZE ? "gameTape.flow.collapsePassedPage" : "gameTape.flow.collapsePassedLanes")}</button>
      <button className="button button--secondary button--compact" type="button" onClick={() => commitCollapsedLaneIds(new Set<string>([...collapsedLaneIds, ...pageLaneIds]))}>{t(tape.repositories.length > GAME_TAPE_FLOW_REPOSITORY_PAGE_SIZE ? "gameTape.flow.collapsePageLanes" : "gameTape.flow.collapseAllLanes")}</button>
      <button className="button button--secondary button--compact" type="button" disabled={!pageLaneIds.some((id) => collapsedLaneIds.has(id))} onClick={() => commitCollapsedLaneIds(new Set<string>([...collapsedLaneIds].filter((id) => !pageLaneIds.includes(id))))}>{t(tape.repositories.length > GAME_TAPE_FLOW_REPOSITORY_PAGE_SIZE ? "gameTape.flow.expandPageLanes" : "gameTape.flow.expandAllLanes")}</button>
    </div> : null}
    <div className="game-tape-flowchart__lanes">
      {pageRepositories.map((repository, localRepositoryIndex) => <RepositoryLane key={repository.repository_execution_id} repository={repository} laneIndex={repositoryPageStart + localRepositoryIndex} laneCount={tape.repositories.length} selectedId={selectedId} currentSourceGroupKey={currentSourceGroupKey} matchStepIds={matchStepIds} collapsed={collapsedLaneIds.has(repository.repository_execution_id)} onToggleCollapsed={() => { const next = new Set<string>(collapsedLaneIds); if (next.has(repository.repository_execution_id)) next.delete(repository.repository_execution_id); else next.add(repository.repository_execution_id); commitCollapsedLaneIds(next); }} onSelect={focusStep} onSelectSourceSegment={selectSourceSegment} />)}
    </div>
    <p className="game-tape-flowchart__truth">{t("gameTape.flow.truth")}<br />{t("gameTape.flow.keyboardHelp")}</p>
  </section>;
}

function RepositoryPager({ page, pageCount, from, to, total, onPage }: { page: number; pageCount: number; from: number; to: number; total: number; onPage(page: number): void }) {
  const { t } = useTranslation();
  return <nav className="game-tape-repository-pagination" aria-label={t("gameTape.repositoryPaginationLabel")}><button className="button button--secondary button--compact" type="button" disabled={page === 0} onClick={() => onPage(Math.max(0, page - 1))}>{t("common.previous")}</button><span>{t("gameTape.repositoryPageRange", { from, to, total })}</span><button className="button button--secondary button--compact" type="button" disabled={page + 1 >= pageCount} onClick={() => onPage(Math.min(pageCount - 1, page + 1))}>{t("common.next")}</button></nav>;
}

function RepositoryScopeConnector({ laneCount }: { laneCount: number }) {
  const count = Math.max(1, laneCount);
  const positions = count === 1 ? [500] : Array.from({ length: count }, (_, index) => 70 + (860 * index) / (count - 1));
  const first = positions[0];
  const last = positions.at(-1) ?? first;
  return <svg className="game-tape-flowchart__scope-connector" data-lanes={laneCount} viewBox="0 0 1000 52" width="100%" height="52" preserveAspectRatio="none" aria-hidden="true" focusable="false">
    <g fill="none" stroke="var(--ice-rink-line)" strokeWidth="2" vectorEffect="non-scaling-stroke">
      <line x1="500" y1="0" x2="500" y2="18" />
      {count > 1 ? <line x1={first} y1="18" x2={last} y2="18" /> : null}
      {positions.map((x) => <line key={x} x1={x} y1="18" x2={x} y2="52" />)}
    </g>
    {positions.map((x) => <circle key={`node:${x}`} cx={x} cy="46" r="4" fill="var(--surface-panel)" stroke="var(--ice-rink-line)" strokeWidth="2" />)}
  </svg>;
}

function RepositoryLane({ repository, laneIndex, laneCount, selectedId, currentSourceGroupKey, matchStepIds, collapsed, onToggleCollapsed, onSelect, onSelectSourceSegment }: { repository: GameTapeRepositoryExecution; laneIndex: number; laneCount: number; selectedId: string | null; currentSourceGroupKey: string | null; matchStepIds: ReadonlySet<string>; collapsed: boolean; onToggleCollapsed(): void; onSelect(id: string): void; onSelectSourceSegment(firstStepExecutionId: string): void }) {
  const { t } = useTranslation();
  const groups = useMemo(() => gameTapeFlowGroups(repository), [repository]);
  const laneSteps = repository.steps ?? [];
  const selectedGroupKey = groups.find((group) => group.steps.some((step) => step.step_execution_id === selectedId))?.key ?? null;
  const activeGroupKey = selectedGroupKey ?? (groups.some((group) => group.key === currentSourceGroupKey) ? currentSourceGroupKey : null);
  const laneStatusCounts = useMemo(() => gameTapeFlowStatusCounts(laneSteps), [laneSteps]);
  const reviewFocus = useMemo(() => gameTapeFlowReviewFocus(repository), [repository]);
  return <section className="game-tape-flowchart__lane" id={laneDomId(repository.repository_execution_id)}>
    <header className="game-tape-flowchart__repository" data-status={repository.status}>
      <div><span className="game-tape-flowchart__type">{t("gameTape.flow.repositoryPosition", { current: laneIndex + 1, total: laneCount })}</span><strong>{repository.repository.name}</strong><small>{t("gameTape.flow.laneMetaDetailed", { steps: laneSteps.length, segments: groups.length })}</small><small>{t("gameTape.flow.recordedDuration", { duration: duration(repository.duration_ms) })}</small><span className="game-tape-flowchart__group-status" role="group" aria-label={t("gameTape.flow.laneStatusSummary")}>{laneStatusCounts.map(({ status, count }) => <StatusBadge key={status} label={t("gameTape.flow.statusCount", { count, status: t(`gameTape.status.${status}`) })} tone={statusTone(status)} />)}</span></div>
      <div className="game-tape-flowchart__repository-actions">
        <StatusBadge label={t(`gameTape.status.${repository.status}`)} tone={statusTone(repository.status)} />
        {reviewFocus ? <button className="text-button" type="button" onClick={() => onSelect(reviewFocus.step_execution_id)}>{t("gameTape.flow.jumpToReview", { status: t(`gameTape.status.${reviewFocus.status}`) })}</button> : null}
        <button className="text-button" type="button" aria-expanded={!collapsed} aria-controls={laneDetailDomId(repository.repository_execution_id)} onClick={onToggleCollapsed}>{collapsed ? t("gameTape.flow.showLaneDetails") : t("gameTape.flow.hideLaneDetails")}</button>
      </div>
    </header>
    {collapsed ? <div className="compact-card" role="status" aria-label={t("gameTape.flow.collapsedLaneSummaryLabel", { repository: repository.repository.name })}>
      <div>
        <span className="eyebrow">{t("gameTape.flow.collapsedLaneEyebrow")}</span>
        <strong>{t("gameTape.flow.collapsedLaneSummary", { steps: laneSteps.length, segments: groups.length })}</strong>
        <small>{t("gameTape.flow.recordedDuration", { duration: duration(repository.duration_ms) })}</small>
        <small>{t("gameTape.flow.collapsedLaneHelp")}</small>
      </div>
      <div className="button-row" role="group" aria-label={t("gameTape.flow.collapsedLaneStatuses")}>{laneStatusCounts.map(({ status, count }) => <StatusBadge key={status} label={t("gameTape.flow.statusCount", { count, status: t(`gameTape.status.${status}`) })} tone={statusTone(status)} />)}</div>
    </div> : null}
    <div id={laneDetailDomId(repository.repository_execution_id)} hidden={collapsed}>
    {groups.length > 1 ? <nav className="game-tape-flowchart__lane-nav" aria-label={t("gameTape.flow.sourceNavigation", { repository: repository.repository.name })}>
      {groups.map((group, index) => { const groupCounts = group.statusCounts; return <button className={["game-tape-flowchart__lane-link", activeGroupKey === group.key ? "game-tape-flowchart__lane-link--selected" : ""].filter(Boolean).join(" ")} type="button" key={group.key} aria-current={activeGroupKey === group.key ? "location" : undefined} onClick={() => { const firstStepExecutionId = group.steps[0]?.step_execution_id; if (firstStepExecutionId) onSelectSourceSegment(firstStepExecutionId); }}>
        <span>{t("gameTape.flow.segmentLink", { index: index + 1, source: group.displayName ?? (group.kind === "goon" ? t("gameTape.flow.repositoryGoon") : group.kind === "source" ? t("gameTape.flow.otherSource") : t("gameTape.flow.resolvedSteps")) })}</span>
        {groupCounts.map(({ status, count }) => <StatusBadge key={status} label={t("gameTape.flow.statusCount", { count, status: t(`gameTape.status.${status}`) })} tone={statusTone(status)} />)}
      </button>; })}
    </nav> : null}
    <div className="game-tape-flowchart__track">
      {groups.map((group, segmentIndex) => <SourceGroup key={group.key} group={group} segmentIndex={segmentIndex} segmentCount={groups.length} laneSteps={laneSteps} bottleneckStepId={repository.bottleneck_step_id ?? null} bottleneckPercent={repository.bottleneck_percent ?? null} selectedId={selectedId} reviewFocusId={reviewFocus?.step_execution_id ?? null} matchStepIds={matchStepIds} onSelect={onSelect} />)}
      <article className="game-tape-flowchart__outcome" data-status={repository.status}>
        <span className="game-tape-flowchart__type">{t("gameTape.flow.outcome")}</span>
        <strong>{t("gameTape.flow.repositoryOutcome")}</strong>
        <StatusBadge label={t(`gameTape.status.${repository.status}`)} tone={statusTone(repository.status)} />
      </article>
    </div>
    </div>
  </section>;
}

function SourceGroup({ group, segmentIndex, segmentCount, laneSteps, bottleneckStepId, bottleneckPercent, selectedId, reviewFocusId, matchStepIds, onSelect }: { group: GameTapeFlowGroup; segmentIndex: number; segmentCount: number; laneSteps: readonly GameTapeStepExecution[]; bottleneckStepId: string | null; bottleneckPercent: number | null; selectedId: string | null; reviewFocusId: string | null; matchStepIds: ReadonlySet<string>; onSelect(id: string): void }) {
  const { t } = useTranslation();
  const groupStatusCounts = group.statusCounts;
  const label = group.kind === "goon"
    ? group.displayName ?? t("gameTape.flow.repositoryGoon")
    : group.kind === "source"
      ? group.displayName ?? t("gameTape.flow.otherSource")
      : t("gameTape.flow.resolvedSteps");
  return <article className="game-tape-flowchart__source" id={sourceGroupDomId(group.key)} data-source-kind={group.kind}>
    <header>
      <span className="game-tape-flowchart__type">{t("gameTape.flow.sourceKindPosition", { kind: t(`gameTape.flow.sourceKind.${group.kind}`), current: segmentIndex + 1, total: segmentCount })}</span>
      <strong>{label}</strong>
      <small>{t("gameTape.flow.groupMeta", { count: group.steps.length })}</small>
      {groupStatusCounts.length > 0 ? <span className="game-tape-flowchart__group-status" role="group" aria-label={t("gameTape.flow.groupStatusSummary")}>{groupStatusCounts.map(({ status, count }) => <StatusBadge key={status} label={t("gameTape.flow.statusCount", { count, status: t(`gameTape.status.${status}`) })} tone={statusTone(status)} />)}</span> : null}
      {group.source ? <small>{group.source}</small> : null}
    </header>
    <ol>
      {group.steps.map((step, groupStepIndex) => { const laneIndex = group.laneStartIndex + groupStepIndex; return <li key={step.step_execution_id}><StepNode step={step} previousStepExecutionId={laneSteps[laneIndex - 1]?.step_execution_id ?? null} nextStepExecutionId={laneSteps[laneIndex + 1]?.step_execution_id ?? null} firstStepExecutionId={laneSteps[0]?.step_execution_id ?? null} lastStepExecutionId={laneSteps.at(-1)?.step_execution_id ?? null} selected={selectedId === step.step_execution_id} reviewFocus={reviewFocusId === step.step_execution_id} searchMatch={matchStepIds.has(step.step_execution_id)} bottleneckPercent={bottleneckStepId === step.step_id ? bottleneckPercent : null} onSelect={onSelect} /></li>; })}
    </ol>
  </article>;
}

function StepNode({ step, previousStepExecutionId, nextStepExecutionId, firstStepExecutionId, lastStepExecutionId, selected, reviewFocus, searchMatch, bottleneckPercent, onSelect }: { step: GameTapeStepExecution; previousStepExecutionId: string | null; nextStepExecutionId: string | null; firstStepExecutionId: string | null; lastStepExecutionId: string | null; selected: boolean; reviewFocus: boolean; searchMatch: boolean; bottleneckPercent: number | null; onSelect(id: string): void }) {
  const { t } = useTranslation();
  const className = ["game-tape-flowchart__step", selected ? "game-tape-flowchart__step--selected" : "", reviewFocus ? "game-tape-flowchart__step--focus" : "", searchMatch ? "game-tape-flowchart__step--match" : "", bottleneckPercent !== null ? "game-tape-flowchart__step--bottleneck" : ""].filter(Boolean).join(" ");
  const keyboardSelect = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const target = event.key === "ArrowLeft" ? previousStepExecutionId : event.key === "ArrowRight" ? nextStepExecutionId : event.key === "Home" ? firstStepExecutionId : event.key === "End" ? lastStepExecutionId : null;
    if (!target || target === step.step_execution_id) return;
    event.preventDefault();
    onSelect(target);
    requestAnimationFrame(() => document.getElementById(stepDomId(target))?.focus());
  };
  return <button
    id={stepDomId(step.step_execution_id)}
    className={className}
    data-status={step.status}
    type="button"
    aria-label={t("gameTape.flow.stepAria", { order: step.order, step: step.step_id, status: t(`gameTape.status.${step.status}`) })}
    aria-keyshortcuts="ArrowLeft ArrowRight Home End"
    onKeyDown={keyboardSelect}
    onClick={() => onSelect(step.step_execution_id)}
  >
    <span className="game-tape-flowchart__order" aria-hidden="true">{step.order}</span>
    <span className="game-tape-flowchart__step-copy"><strong>{step.step_id}</strong><small>{step.kind}</small>{reviewFocus ? <small className="game-tape-flowchart__focus-label">{t("gameTape.flow.reviewFocus")}</small> : null}{bottleneckPercent !== null ? <small className="game-tape-flowchart__signal-label">{t("gameTape.flow.bottleneck", { percent: Math.round(bottleneckPercent) })}</small> : null}{(step.attempt_count ?? 0) > 1 ? <small className="game-tape-flowchart__signal-label">{t("gameTape.flow.attemptCount", { count: step.attempt_count ?? 0 })}</small> : null}</span>
    <span className="game-tape-flowchart__step-status"><span className="game-tape-flowchart__step-status-line"><b className="game-tape-status-mark" data-status={step.status} aria-hidden="true">{statusSymbol(step.status)}</b><StatusBadge label={t(`gameTape.status.${step.status}`)} tone={statusTone(step.status)} /></span><small>{duration(step.duration_ms)}</small></span>
  </button>;
}

function laneDetailDomId(repositoryExecutionId: string): string {
  return `game-tape-flow-lane-details-${repositoryExecutionId}`;
}

function laneDomId(repositoryExecutionId: string): string {
  return `game-tape-flow-lane-${repositoryExecutionId}`;
}

function sourceGroupDomId(groupKey: string): string {
  return `game-tape-flow-source-${groupKey}`;
}

function stepDomId(stepExecutionId: string): string {
  return `game-tape-flow-step-${stepExecutionId}`;
}

function preferredScrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}
