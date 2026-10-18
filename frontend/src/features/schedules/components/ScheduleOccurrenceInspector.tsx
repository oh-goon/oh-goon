import { useTranslation } from "react-i18next";

import { ScheduleOccurrenceFilter, ScheduleRepositoryRunStatus } from "@generated/enums";
import type { ScheduleOccurrenceRepositoryPage } from "@generated/models";
import type { ScheduleOccurrenceFilterValue } from "@/features/schedules/hooks/useScheduleOccurrenceRepositories";
import { StatusBadge } from "@/components/common/StatusBadge";

interface Props {
  scheduleName: string;
  page: ScheduleOccurrenceRepositoryPage | null;
  loading: boolean;
  status: ScheduleOccurrenceFilterValue;
  offset: number;
  onStatusChange: (status: ScheduleOccurrenceFilterValue) => void;
  onOffsetChange: (offset: number) => void;
  onOpenRun: (runId: string) => void;
  onClose: () => void;
}

const FILTERS = [
  ScheduleOccurrenceFilter.ALL,
  ScheduleOccurrenceFilter.SUCCESSFUL,
  ScheduleOccurrenceFilter.FAILED,
  ScheduleOccurrenceFilter.RUNNING,
  ScheduleOccurrenceFilter.PENDING,
  ScheduleOccurrenceFilter.NOT_STARTED,
  ScheduleOccurrenceFilter.ATTENTION_REQUIRED,
  ScheduleOccurrenceFilter.CANCELLED,
] as const;

export function ScheduleOccurrenceInspector({
  scheduleName,
  page,
  loading,
  status,
  offset,
  onStatusChange,
  onOffsetChange,
  onOpenRun,
  onClose,
}: Props) {
  const { t } = useTranslation();
  const items = page?.items ?? [];
  const end = page ? Math.min(page.total, page.offset + items.length) : 0;
  return <section className="panel schedule-occurrence-inspector" aria-label={t("schedules.occurrence.inspectorTitle")}>
    <div className="section-heading"><div><span className="eyebrow">{t("schedules.occurrence.inspectorEyebrow")}</span><h3>{scheduleName}</h3><p>{t("schedules.occurrence.inspectorDescription")}</p></div><button className="text-button" type="button" onClick={onClose}>{t("common.close")}</button></div>
    <div className="repository-filters schedule-occurrence-filters" role="group" aria-label={t("schedules.occurrence.filterLabel")}>
      {FILTERS.map((filter) => <button key={filter} className={status === filter ? "filter-chip filter-chip--active" : "filter-chip"} type="button" onClick={() => { onStatusChange(filter); onOffsetChange(0); }}>{t(`schedules.occurrence.filter.${filter}`)}</button>)}
    </div>
    {loading && page === null ? <p className="muted loading-state" role="status">{t("common.loading")}</p> : null}
    {page && page.total === 0 ? <p className="muted">{t("schedules.occurrence.noRepositoryRuns")}</p> : null}
    {page && page.total > 0 ? <>
      <div className="table-scroll schedule-occurrence-table"><table className="data-table"><thead><tr><th>{t("schedules.occurrence.repository")}</th><th>{t("schedules.occurrence.state")}</th><th>{t("schedules.occurrence.reason")}</th><th>{t("schedules.occurrence.run")}</th></tr></thead><tbody>
        {items.map((item) => <tr key={item.repository_id}>
          <td><code>{item.repository_id}</code></td>
          <td><StatusBadge label={t(`schedules.repositoryState.${item.status}`)} tone={item.status === ScheduleRepositoryRunStatus.SUCCESSFUL ? "success" : item.status === ScheduleRepositoryRunStatus.RUNNING || item.status === ScheduleRepositoryRunStatus.PENDING ? "info" : item.status === ScheduleRepositoryRunStatus.NOT_STARTED || item.status === ScheduleRepositoryRunStatus.FAILED ? "warning" : "neutral"} /></td>
          <td>{item.failure ? <span>{item.failure.code}: {item.failure.message}</span> : "—"}</td>
          <td>{item.fleet_run_id ? <button className="text-button" type="button" onClick={() => onOpenRun(item.fleet_run_id!)}>{t("schedules.gameTape")}</button> : <span className="muted">{t("schedules.occurrence.noRun")}</span>}</td>
        </tr>)}
      </tbody></table></div>
      <div className="button-row schedule-occurrence-pagination">
        <button className="button button--quiet button--small" type="button" disabled={offset === 0} onClick={() => onOffsetChange(Math.max(0, offset - 50))}>{t("common.previous")}</button>
        <span className="muted" role="status">{t("schedules.occurrence.pageRange", { start: page.offset + 1, end, total: page.total })}</span>
        <button className="button button--quiet button--small" type="button" disabled={page.offset + page.limit >= page.total} onClick={() => onOffsetChange(offset + 50)}>{t("common.next")}</button>
      </div>
    </> : null}
  </section>;
}
