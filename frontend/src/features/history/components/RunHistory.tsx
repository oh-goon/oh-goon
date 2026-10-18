import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type {
  FleetRunDetail,
  FleetRunHistoryItem,
  FleetSummary,
} from "@generated/models";
import { StatusBadge } from "@/components/common/StatusBadge";
import { RunDetail } from "@/features/history/components/RunDetail";
import { runHistoryFilterOutcome, searchRunHistory, type RunHistoryFilter } from "@/features/history/runHistoryFilter";
import { actionKey, outcomeKey, outcomeTone } from "@/domain/presentation/operations";
import { formatRunDate, formatRunRecordedDuration, runFailureSummary, runTargetDisplay } from "@/features/history/runHistoryPresentation";
import { runLineage } from "@/domain/presentation/runProvenance";

interface RunHistoryProps {
  items: FleetRunHistoryItem[];
  selected: FleetRunDetail | null;
  loading: boolean;
  detailLoading: boolean;
  hasMore: boolean;
  summary: FleetSummary | null;
  actionsDisabled: boolean;
  query?: string;
  filter?: RunHistoryFilter;
  onQueryChange?: (query: string) => void;
  onFilterChange?: (filter: RunHistoryFilter) => void;
  onSelect: (runId: string) => void;
  onLoadMore: () => void;
  onClearSelection: () => void;
  onRunAgain: (runId: string) => void;
  onReadmit: (runId: string) => void;
  onOpenRecovery: () => void;
}

export function RunHistory({
  items,
  selected,
  loading,
  detailLoading,
  hasMore,
  summary,
  actionsDisabled,
  query = "",
  filter = "all",
  onQueryChange,
  onFilterChange,
  onSelect,
  onLoadMore,
  onClearSelection,
  onRunAgain,
  onReadmit,
  onOpenRecovery,
}: RunHistoryProps) {
  const { t } = useTranslation();
  const changeQuery = (next: string) => { onQueryChange?.(next); };
  const changeFilter = (next: RunHistoryFilter) => { onFilterChange?.(next); };
  const visibleItems = useMemo(() => {
    const outcome = runHistoryFilterOutcome(filter);
    const filtered = outcome === null ? items : items.filter((item) => item.outcome === outcome);
    return searchRunHistory(filtered, query);
  }, [items, query, filter]);

  return (
    <section id="run-history" className="panel history-panel" aria-label={t("history.pageTitle")}>
      <div className="section-heading">
        <div>
          <span className="eyebrow">{t("history.eyebrow")}</span>
          <h2>{t("history.title")}</h2>
          <p>{t("history.description")}</p>
        </div>
      </div>

      {items.length > 0 ? <div className="history-toolbar">
        <label><span>{t("history.search")}</span><input type="search" value={query} onChange={(event) => changeQuery(event.target.value)} placeholder={t("history.searchPlaceholder")} /></label>
        <div className="repository-filters" role="group" aria-label={t("history.filters.label")}>
          {(["all", "success", "attention", "failed", "active"] as const).map((item) => <button className={filter === item ? "filter-chip filter-chip--active" : "filter-chip"} key={item} type="button" onClick={() => changeFilter(item)}>{t(`history.filters.${item}`)}</button>)}
        </div>
        <span className="muted" role="status">{t("history.visibleCount", { visible: visibleItems.length, total: items.length })}</span>
      </div> : null}

      {items.length > 0 && visibleItems.length === 0 ? <div className="empty-inline history-no-matches"><span>{t("history.noMatches")}</span><button className="text-button" type="button" onClick={() => { changeQuery(""); changeFilter("all"); }}>{t("common.clearFilters")}</button>{hasMore ? <button className="button button--secondary history-load-more" type="button" disabled={loading} onClick={onLoadMore}>{loading ? t("history.loading") : t("history.loadMore")}</button> : null}</div> : null}
      {visibleItems.length > 1 ? <details className="advanced-disclosure" aria-label={t("history.runGrid.title")}><summary>{t("history.runGrid.title")}</summary><p className="muted">{t("history.runGrid.help")}</p><RunSummaryGrid items={visibleItems} onSelect={onSelect} /></details> : null}
      {visibleItems.length > 0 ? (
        <div className={selected ? "history-layout history-layout--detail" : "history-layout"}>
          <ul className="history-list">
            {visibleItems.map((item) => {
              const recordedDuration = formatRunRecordedDuration(item);
              const failure = runFailureSummary(item);
              const lineage = runLineage(item.execution);
              return <li key={item.run.fleet_run_id}>
                <button
                  className={`history-row${selected?.run.fleet_run_id === item.run.fleet_run_id ? " history-row--selected" : ""}`}
                  type="button"
                  onClick={() => onSelect(item.run.fleet_run_id)}
                >
                  <span className="history-row-main">
                    <strong>{runTargetDisplay(item, t("history.repositoryCount", { count: item.run.targets.length }))}</strong>
                    <small>{t(actionKey(item.run.capability.action))} · {formatRunDate(item.run.accepted_at)}</small>
                    {lineage ? <small>{t(`history.lineage.${lineage.kind}`, { runId: lineage.parentRunId })}</small> : null}
                    {failure ? <small className="history-row-failure">{failure}</small> : null}
                  </span>
                  <span className="history-row-result">
                    <StatusBadge label={t(outcomeKey(item.outcome))} tone={outcomeTone(item.outcome)} />
                    <strong className="history-row-duration">{recordedDuration ?? t("history.activeDuration")}</strong>
                    <small>{item.run.progress.completed} / {item.run.progress.total}</small>
                  </span>
                </button>
              </li>;
            })}
            {hasMore ? (
              <li className="history-list__more"><button className="button button--secondary history-load-more" type="button" disabled={loading} onClick={onLoadMore}>
                {loading ? t("history.loading") : t("history.loadMore")}
              </button></li>
            ) : null}
          </ul>

          {selected || detailLoading ? <div className="history-detail-shell">
            {selected ? <button className="text-button history-mobile-back" type="button" onClick={onClearSelection}>← {t("history.backToRuns")}</button> : null}
            {detailLoading ? <p>{t("history.loadingDetail")}</p> : null}
            {!detailLoading && selected ? (
              <RunDetail
                detail={selected}
                summary={summary}
                actionsDisabled={actionsDisabled}
                onRunAgain={onRunAgain}
                onReadmit={onReadmit}
                onOpenRecovery={onOpenRecovery}
              />
            ) : null}
          </div> : null}
        </div>
      ) : null}
    </section>
  );
}

function RunSummaryGrid({ items, onSelect }: { items: readonly FleetRunHistoryItem[]; onSelect(runId: string): void }) {
  const { t } = useTranslation();
  return <div className="table-shell" style={{ overflowX: "auto" }}><table style={{ minWidth: "760px" }}>
    <thead><tr><th scope="col">{t("history.runGrid.metric")}</th>{items.map((item) => <th scope="col" key={item.run.fleet_run_id}><button className="text-button" type="button" onClick={() => onSelect(item.run.fleet_run_id)}>{runTargetDisplay(item, t("history.repositoryCount", { count: item.run.targets.length }))}</button><small>{formatRunDate(item.run.accepted_at)}</small></th>)}</tr></thead>
    <tbody>
      <tr><th scope="row">{t("history.runGrid.source")}</th>{items.map((item) => { const lineage = runLineage(item.execution); return <td key={item.run.fleet_run_id}>{t(`history.sources.${item.run.source ?? "MANUAL"}`)}{lineage ? <small>{t(`history.lineage.${lineage.kind}`, { runId: lineage.parentRunId })}</small> : null}</td>; })}</tr>
      <tr><th scope="row">{t("history.runGrid.outcome")}</th>{items.map((item) => <td key={item.run.fleet_run_id}><StatusBadge label={t(outcomeKey(item.outcome))} tone={outcomeTone(item.outcome)} /></td>)}</tr>
      <tr><th scope="row">{t("history.runGrid.duration")}</th>{items.map((item) => <td key={item.run.fleet_run_id}><strong>{formatRunRecordedDuration(item) ?? t("history.activeDuration")}</strong></td>)}</tr>
      <tr><th scope="row">{t("history.runGrid.failure")}</th>{items.map((item) => <td key={item.run.fleet_run_id}>{runFailureSummary(item) ?? "—"}</td>)}</tr>
      <tr><th scope="row">{t("history.runGrid.failedGoon")}</th>{items.map((item) => <td key={item.run.fleet_run_id}>{item.failure_identity?.goon_name ?? "—"}</td>)}</tr>
      <tr><th scope="row">{t("history.runGrid.failedStep")}</th>{items.map((item) => <td key={item.run.fleet_run_id}>{item.failure_identity?.step_id ?? "—"}</td>)}</tr>
      <tr><th scope="row">{t("history.runGrid.progress")}</th>{items.map((item) => <td key={item.run.fleet_run_id}>{item.run.progress.completed} / {item.run.progress.total}</td>)}</tr>
      <tr><th scope="row">{t("history.runGrid.accepted")}</th>{items.map((item) => <td key={item.run.fleet_run_id}>{formatRunDate(item.run.accepted_at)}</td>)}</tr>
    </tbody>
  </table></div>;
}
