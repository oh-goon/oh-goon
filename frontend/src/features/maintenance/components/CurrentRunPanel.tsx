import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Verdict as VerdictValue } from "@generated/enums";
import type { FleetRunDetail, FleetRunState, OperationReport } from "@generated/models";
import { StatusBadge } from "@/components/common/StatusBadge";
import { actionKey, verdictKey, verdictTone } from "@/domain/presentation/operations";
import { canRequestFleetRunCancel, isFleetRunCancelling } from "@/domain/fleet/runLifecycle";

type ResultFilter = "all" | "passed" | "failed" | "attention" | "waiting";

interface BaseProps {
  run: FleetRunState;
  detail: FleetRunDetail | null;
  onInspect: () => void;
  onInspectRepository?: (repositoryId: string) => void;
}

type Props = BaseProps & (
  | { mode: "active"; onCancel: (runId: string) => void }
  | { mode: "completed"; onCancel?: never }
);

const RESULT_PAGE_SIZE = 100;

export function CurrentRunPanel(props: Props) {
  const { run, detail, mode, onInspect, onInspectRepository } = props;
  const onCancel = mode === "active" ? props.onCancel : null;
  const { t } = useTranslation();
  const [confirmCancelRunId, setConfirmCancelRunId] = useState<string | null>(null);
  const [filter, setFilter] = useState<ResultFilter>("all");
  const [page, setPage] = useState(0);
  const ownedDetail = detail?.run.fleet_run_id === run.fleet_run_id ? detail : null;
  const active = mode === "active";
  const canCancel = active && canRequestFleetRunCancel(run);
  const cancelling = active && isFleetRunCancelling(run);
  const confirmCancel = canCancel && confirmCancelRunId === run.fleet_run_id;
  const completed = useMemo(
    () => new Map((ownedDetail?.operations ?? []).map((report) => [report.repository.repository_id, report])),
    [ownedDetail?.operations],
  );

  useEffect(() => {
    setConfirmCancelRunId(null);
    setFilter("all");
    setPage(0);
  }, [run.fleet_run_id]);

  const percent = run.progress.total === 0 ? 0 : Math.max(0, Math.min(100, Math.round((run.progress.completed / run.progress.total) * 100)));
  const remaining = Math.max(0, run.progress.total - run.progress.completed);
  const attention = Math.max(0, (run.progress.needs_attention ?? 0) - run.progress.failed);
  const visibleTargets = run.targets.filter((target) => matchesResultFilter(completed.get(target.repository_id), filter));
  const pageCount = Math.max(1, Math.ceil(visibleTargets.length / RESULT_PAGE_SIZE));
  const boundedPage = Math.min(page, pageCount - 1);
  useEffect(() => { if (page !== boundedPage) setPage(boundedPage); }, [boundedPage, page]);
  const start = boundedPage * RESULT_PAGE_SIZE;
  const pageTargets = visibleTargets.slice(start, start + RESULT_PAGE_SIZE);

  const chooseFilter = (next: ResultFilter) => {
    setFilter(next);
    setPage(0);
  };

  return <section className="panel current-run-panel" aria-label={t("currentRun.title")}>
    <div className="section-heading"><div><span className="eyebrow">{t("currentRun.eyebrow")}</span><h2>{active ? t("currentRun.title") : t("currentRun.latestTitle")}</h2><p>{t(actionKey(run.capability.action))} · {run.progress.completed} / {run.progress.total}</p></div><div className="current-run-actions"><button className="button button--primary" type="button" onClick={onInspect}>{t("currentRun.inspect")}</button>{active && (canCancel || cancelling) ? confirmCancel && canCancel ? <><button className="button button--danger" type="button" onClick={() => { setConfirmCancelRunId(null); onCancel?.(run.fleet_run_id); }}>{t("currentRun.confirmCancel")}</button><button className="text-button" type="button" onClick={() => setConfirmCancelRunId(null)}>{t("currentRun.keepRunning")}</button></> : <button className="button button--secondary" type="button" disabled={cancelling} onClick={() => setConfirmCancelRunId(run.fleet_run_id)}>{cancelling ? t("operations.cancelling") : t("currentRun.cancelRun")}</button> : null}</div></div>
    {active ? <p className="muted" role="status">{t("currentRun.blocksNewRuns")}</p> : null}
    <div className="progress-track" role="progressbar" aria-label={t("currentRun.progress")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{ width: `${percent}%` }} /></div>

    <div className="current-run-summary" role="group" aria-label={t("currentRun.summaryLabel")} data-testid="current-run-summary">
      <ResultCountButton label={t("currentRun.passed")} count={run.progress.succeeded} selected={filter === "passed"} disabled={ownedDetail === null} onClick={() => chooseFilter("passed")} />
      <ResultCountButton label={t("currentRun.failed")} count={run.progress.failed} selected={filter === "failed"} disabled={ownedDetail === null} onClick={() => chooseFilter("failed")} />
      {attention > 0 ? <ResultCountButton label={t("currentRun.attention")} count={attention} selected={filter === "attention"} disabled={ownedDetail === null} onClick={() => chooseFilter("attention")} /> : null}
      {remaining > 0 ? <ResultCountButton label={t("currentRun.remaining")} count={remaining} selected={filter === "waiting"} disabled={ownedDetail === null} onClick={() => chooseFilter("waiting")} /> : null}
    </div>
    {ownedDetail !== null && filter !== "all" ? <button className="text-button current-run-show-all" type="button" onClick={() => chooseFilter("all")}>{t("currentRun.showAll", { count: run.progress.total })}</button> : null}


    {ownedDetail === null ? <p className="muted" role="status">{t("currentRun.detailUnavailable")}</p> : <div className="current-run-list" role="list" aria-label={t("currentRun.listLabel")} data-testid="current-run-list">
      {pageTargets.map((target) => {
        const report = completed.get(target.repository_id);
        return <div className="current-run-row" role="listitem" key={target.repository_id}><div><strong>{target.name}</strong><small>{target.path}</small></div><div className="current-run-row__actions">{report ? <StatusBadge label={t(verdictKey(report.outcome.verdict ?? VerdictValue.UNKNOWN))} tone={verdictTone(report.outcome.verdict ?? VerdictValue.UNKNOWN)} /> : <StatusBadge label={active ? t("currentRun.waiting") : t("history.notCompleted")} tone="neutral" />}{onInspectRepository ? <button className="text-button" type="button" onClick={() => onInspectRepository(target.repository_id)}>{t("currentRun.openRepository")}</button> : null}</div></div>;
      })}
    </div>}
    {ownedDetail !== null && visibleTargets.length > RESULT_PAGE_SIZE ? <nav className="current-run-pagination" aria-label={t("currentRun.resultPaginationLabel")}><button className="button button--secondary button--small" type="button" disabled={boundedPage === 0} onClick={() => setPage(Math.max(0, boundedPage - 1))}>{t("common.previous")}</button><span>{t("currentRun.resultRange", { from: start + 1, to: Math.min(start + pageTargets.length, visibleTargets.length), total: visibleTargets.length })}</span><button className="button button--secondary button--small" type="button" disabled={boundedPage + 1 >= pageCount} onClick={() => setPage(Math.min(pageCount - 1, boundedPage + 1))}>{t("common.next")}</button></nav> : null}
  </section>;
}

function ResultCountButton({ label, count, selected, disabled, onClick }: { label: string; count: number; selected: boolean; disabled: boolean; onClick: () => void }) {
  return <button className="current-run-summary__item" type="button" aria-pressed={selected} disabled={disabled} onClick={onClick}><strong>{count}</strong><span>{label}</span></button>;
}

function matchesResultFilter(report: OperationReport | undefined, filter: ResultFilter): boolean {
  if (filter === "all") return true;
  if (!report) return filter === "waiting";
  if (filter === "passed") return report.outcome.state === "successful";
  if (filter === "failed") return report.outcome.state === "failed";
  if (filter === "attention") return report.outcome.state === "completed_attention" || report.outcome.state === "failed_attention" || report.outcome.state === "blocked";
  return false;
}
