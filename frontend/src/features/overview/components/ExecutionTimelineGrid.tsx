import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { FleetExecutionTimeline, RepositoryExecutionBucket } from "@generated/models";
import { statusSymbol } from "@/features/game-tape/GameTapeFeature";
import { executionObservationWindow, nextExecutionObservationBoundary, type ExecutionObservationBucket } from "@/features/overview/executionObservationWindow";
import { useExecutionTimeline } from "@/features/overview/hooks/useExecutionTimeline";

interface Props {
  live: boolean;
  state: ExecutionTimelineViewState;
  onStateChange(state: ExecutionTimelineViewState): void;
  onOpenRuns(repositoryId: string, fromAt: string, toAt: string, planQuery: string): void;
}

export type ExecutionTimelineViewState = Readonly<{
  bucket: ExecutionObservationBucket;
  planQuery: string;
  repositoryQuery: string;
  offset: number;
}>;

export function ExecutionTimelineGrid({ live, state, onStateChange, onOpenRuns }: Props) {
  const { t, i18n } = useTranslation();
  const { bucket, planQuery, repositoryQuery, offset } = state;
  const requestPlanQuery = useDebouncedValue(planQuery, 250);
  const requestRepositoryQuery = useDebouncedValue(repositoryQuery, 250);
  const [windowRevision, setWindowRevision] = useState(0);
  const range = useMemo(() => executionObservationWindow(bucket), [bucket, windowRevision]);
  useEffect(() => {
    const boundaryAt = nextExecutionObservationBoundary(bucket).getTime();
    const delay = Math.max(1, boundaryAt - Date.now() + 50);
    const timer = window.setTimeout(() => setWindowRevision((value) => value + 1), delay);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible" && Date.now() >= boundaryAt) {
        setWindowRevision((value) => value + 1);
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [bucket, windowRevision]);
  const resource = useExecutionTimeline(bucket, range.fromAt, range.toAt, requestPlanQuery, requestRepositoryQuery, offset, live);
  const data = resource.data;
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const setGranularity = (value: ExecutionObservationBucket) => onStateChange({ ...state, bucket: value, offset: 0 });
  const setPlan = (value: string) => onStateChange({ ...state, planQuery: value, offset: 0 });
  const setRepository = (value: string) => onStateChange({ ...state, repositoryQuery: value, offset: 0 });

  return <section className="panel execution-timeline" aria-label={t("executionOverview.timeline.title")}>
    <div className="section-heading"><div><span className="eyebrow">{t("executionOverview.timeline.eyebrow")}</span><h2>{t("executionOverview.timeline.title")}</h2><p>{t("executionOverview.timeline.description")}</p></div></div>
    <div className="execution-timeline__controls">
      <fieldset className="segmented-control"><legend className="sr-only">{t("executionOverview.timeline.granularity")}</legend>{(["day", "week", "month"] as const).map((value) => <button key={value} type="button" aria-pressed={bucket === value} className={`segmented-control__item${bucket === value ? " segmented-control__item--active" : ""}`} onClick={() => setGranularity(value)}>{t(`executionOverview.timeline.${value}`)}</button>)}</fieldset>
      <label><span>{t("executionOverview.timeline.plan")}</span><input value={planQuery} onChange={(event) => setPlan(event.target.value)} placeholder={t("executionOverview.timeline.planPlaceholder")} /></label>
      <label><span>{t("executionOverview.timeline.repository")}</span><input value={repositoryQuery} onChange={(event) => setRepository(event.target.value)} placeholder={t("executionOverview.timeline.repositoryPlaceholder")} /></label>
    </div>
    {resource.status === "read-error" ? <p className="notice notice--warning" role="alert">{resource.failure?.message}</p> : null}
    {resource.status === "read-loading" && !data ? <p className="muted loading-state" role="status">{t("executionOverview.timeline.loading")}</p> : null}
    {data ? <TimelineTable data={data} locale={locale} planQuery={requestPlanQuery} onOpenRuns={onOpenRuns} t={t} /> : null}
    {data && data.total_repositories === 0 ? <p className="muted">{t("executionOverview.timeline.empty")}</p> : null}
    {data && data.total_repositories > 0 ? <div className="execution-timeline__pager"><span>{t("executionOverview.timeline.page", { from: data.offset + 1, to: Math.min(data.offset + (data.items ?? []).length, data.total_repositories), total: data.total_repositories })}</span><div className="button-row"><button className="button button--quiet" type="button" disabled={data.offset === 0} onClick={() => onStateChange({ ...state, offset: Math.max(0, data.offset - data.limit) })}>{t("common.previous")}</button><button className="button button--quiet" type="button" disabled={data.offset + (data.items ?? []).length >= data.total_repositories} onClick={() => onStateChange({ ...state, offset: data.offset + data.limit })}>{t("common.next")}</button></div></div> : null}
  </section>;
}

function TimelineTable({ data, locale, planQuery, onOpenRuns, t }: { data: FleetExecutionTimeline; locale: string; planQuery: string; onOpenRuns: Props["onOpenRuns"]; t: ReturnType<typeof useTranslation>["t"] }) {
  const rows = useMemo(() => (data.items ?? []).map((row) => ({
    row,
    buckets: new Map((row.buckets ?? []).map((item) => [item.start_at, item])),
  })), [data.items]);
  return <div className="execution-timeline__scroll"><table className="execution-timeline__grid"><thead><tr><th scope="col">{t("executionOverview.timeline.repository")}</th>{data.windows.map((window) => <th scope="col" key={window.start_at}>{formatBucket(window.start_at, data.bucket, locale)}</th>)}</tr></thead><tbody>{rows.map(({ row, buckets }) => <tr key={row.repository_id}><th scope="row"><strong title={row.repository_name}>{row.repository_name}</strong><small title={row.repository_id}>{row.repository_id}</small></th>{data.windows.map((window) => <td key={window.start_at}><TimelineCell bucket={buckets.get(window.start_at)} onOpen={() => onOpenRuns(row.repository_id, window.start_at, window.end_at, planQuery)} t={t} /></td>)}</tr>)}</tbody></table></div>;
}

function TimelineCell({ bucket, onOpen, t }: { bucket?: RepositoryExecutionBucket; onOpen(): void; t: ReturnType<typeof useTranslation>["t"] }) {
  if (!bucket) return <span className="execution-timeline__empty" role="img" aria-label={t("executionOverview.timeline.noExecution")}>·</span>;
  const label = bucket.statuses.map((item) => `${t(`gameTape.status.${item.status}`)} ${item.count}`).join(", ");
  return <button type="button" className="execution-timeline__cell" onClick={onOpen} aria-label={t("executionOverview.timeline.openCell", { runs: bucket.run_count, statuses: label })} title={label}>{bucket.statuses.map((item) => <span key={item.status} className="execution-timeline__status" data-status={item.status}><span className="execution-timeline__status-symbol" aria-hidden="true">{statusSymbol(item.status)}</span><b>{item.count}</b><small>{t(`gameTape.status.${item.status}`)}</small></span>)}</button>;
}

function formatBucket(value: string, bucket: FleetExecutionTimeline["bucket"], locale: string): string {
  const date = new Date(value);
  if (bucket === "month") return new Intl.DateTimeFormat(locale, { month: "short", year: "2-digit", timeZone: "UTC" }).format(date);
  if (bucket === "week") return new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: "UTC" }).format(date);
  return new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", timeZone: "UTC" }).format(date);
}

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);
  return debounced;
}
