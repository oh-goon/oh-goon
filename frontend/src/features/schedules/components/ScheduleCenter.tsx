import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { ScheduleExecutionStatus, ScheduleOccurrenceFilter, ScheduleState } from "@generated/enums";
import type { LeagueMap, ScheduleCatalog, ScheduleDefinition, ScheduleReplace } from "@generated/models";
import { StatusBadge } from "@/components/common/StatusBadge";
import { isScheduleExecutionActive, scheduleFrequencyMinutes, scheduleFrequencyPreset } from "@/features/schedules/model";
import { ScheduleFrequencyFields, type ScheduleFrequencyChoice } from "@/features/schedules/components/ScheduleFrequencyFields";
import { ScheduleOccurrenceInspector } from "@/features/schedules/components/ScheduleOccurrenceInspector";
import { useScheduleOccurrenceRepositories, type ScheduleOccurrenceFilterValue } from "@/features/schedules/hooks/useScheduleOccurrenceRepositories";

type StateFilter = "all" | "active" | "paused";
type OccurrenceSelection = { scheduleId: string; scheduleRunId: string; scheduleName: string } | null;

interface Props {
  catalog: ScheduleCatalog | null;
  loading?: boolean;
  league?: LeagueMap | null;
  actionsDisabled: boolean;
  onUpdate: (scheduleId: string, schedule: ScheduleReplace) => void;
  onDelete: (definition: ScheduleDefinition) => void;
  onRun: (scheduleId: string) => void;
  onOpenRun: (fleetRunId: string) => void;
}

function editable(definition: ScheduleDefinition): ScheduleReplace {
  return {
    generation_id: definition.generation_id,
    record_version: definition.record_version,
    name: definition.name,
    repository_ids: definition.repository_ids,
    repository_instance_ids: definition.repository_instance_ids,
    target: definition.target,
    interval_minutes: definition.interval_minutes,
    state: definition.state,
  };
}
function toggledState(definition: ScheduleDefinition): ScheduleReplace["state"] {
  return definition.state === ScheduleState.ACTIVE ? ScheduleState.PAUSED : ScheduleState.ACTIVE;
}
function formatDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}
function scheduleDefinitionIdentity(definition: ScheduleDefinition): string {
  return canonicalIdentity({
    scheduleId: definition.schedule_id,
    generationId: definition.generation_id,
    recordVersion: definition.record_version,
  });
}

export function ScheduleCenter({ catalog, loading = false, league = null, actionsDisabled, onUpdate, onDelete, onRun, onOpenRun }: Props) {
  const { t } = useTranslation();
  const [editingIdentity, setEditingIdentity] = useState<string | null>(null);
  const [confirmingDeleteIdentity, setConfirmingDeleteIdentity] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftFrequency, setDraftFrequency] = useState<ScheduleFrequencyChoice>("daily");
  const [draftCustomInterval, setDraftCustomInterval] = useState("60");
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [occurrence, setOccurrence] = useState<OccurrenceSelection>(null);
  const [occurrenceFilter, setOccurrenceFilter] = useState<ScheduleOccurrenceFilterValue>(ScheduleOccurrenceFilter.ALL);
  const [occurrenceOffset, setOccurrenceOffset] = useState(0);
  const occurrenceRows = useScheduleOccurrenceRepositories(occurrence?.scheduleId ?? null, occurrence?.scheduleRunId ?? null, occurrenceFilter, occurrenceOffset);
  useEffect(() => {
    const page = occurrenceRows.data;
    if (!page) return;
    const maxOffset = page.total === 0 ? 0 : Math.floor((page.total - 1) / page.limit) * page.limit;
    if (occurrenceOffset > maxOffset) setOccurrenceOffset(maxOffset);
  }, [occurrenceOffset, occurrenceRows.data]);
  const repositoryNames = useMemo(() => new Map((league?.teams ?? []).map((team) => [team.repository.repository_id, team.repository.name])), [league]);
  const schedules = catalog?.schedules ?? [];
  const visibleSchedules = schedules.filter((item) => {
    const active = item.definition.state === ScheduleState.ACTIVE;
    if (stateFilter === "active" && !active) return false;
    if (stateFilter === "paused" && active) return false;
    if (!query.trim()) return true;
    const repositories = item.definition.repository_ids.map((id) => repositoryNames.get(id) ?? id).join(" ");
    return `${item.definition.name} ${item.definition.schedule_id} ${repositories} ${item.definition.target.plan.name}`.toLowerCase().includes(query.trim().toLowerCase());
  });
  const selectOccurrencePopulation = (filter: ScheduleOccurrenceFilterValue, nextOccurrence: OccurrenceSelection = occurrence) => {
    setOccurrence(nextOccurrence);
    setOccurrenceFilter(filter);
    setOccurrenceOffset(0);
  };
  const inspect = (definition: ScheduleDefinition, scheduleRunId: string, filter: ScheduleOccurrenceFilterValue) => {
    selectOccurrencePopulation(filter, { scheduleId: definition.schedule_id, scheduleRunId, scheduleName: definition.name });
  };

  return <section className="panel schedule-panel" aria-label={t("schedules.title")}>
    <div className="section-heading"><div><span className="eyebrow">{t("schedules.eyebrow")}</span><h2>{t("schedules.title")}</h2><p>{t("schedules.description")}</p></div></div>
    {loading && catalog === null ? <p className="muted-text loading-state" role="status">{t("schedules.loading")}</p> : null}
    {!loading && schedules.length === 0 ? <p className="muted-text">{t("schedules.empty")}</p> : null}
    {schedules.length > 0 ? <div className="schedule-toolbar"><label><span>{t("schedules.search")}</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("schedules.searchPlaceholder")} /></label><div className="repository-filters" role="group" aria-label={t("schedules.filters.label")}>{(["all", "active", "paused"] as const).map((filter) => <button className={stateFilter === filter ? "filter-chip filter-chip--active" : "filter-chip"} type="button" key={filter} onClick={() => setStateFilter(filter)}>{t(`schedules.filters.${filter}`)}</button>)}</div><span className="muted" role="status">{t("schedules.visibleCount", { visible: visibleSchedules.length, total: schedules.length })}</span></div> : null}
    {schedules.length > 0 && visibleSchedules.length === 0 ? <div className="empty-inline"><span>{t("schedules.noMatches")}</span><button className="text-button" type="button" onClick={() => { setQuery(""); setStateFilter("all"); }}>{t("common.clearFilters")}</button></div> : null}
    <div className="schedule-list">{visibleSchedules.map((item) => {
      const latest = item.latest_occurrence;
      const status = latest?.status ?? null;
      const nextRun = formatDate(item.next_run_at);
      const frequency = scheduleFrequencyPreset(item.definition.interval_minutes);
      const frequencyLabel = frequency === "custom" ? t("schedules.frequency.minutes", { count: item.definition.interval_minutes }) : t(`schedules.frequency.${frequency}`);
      const definitionIdentity = scheduleDefinitionIdentity(item.definition);
      const confirmingDelete = confirmingDeleteIdentity === definitionIdentity;
      const editing = editingIdentity === definitionIdentity;
      const missingOccurrence = formatDate(item.missing_expected_occurrence_at);
      return <article className="recipe-card schedule-card" key={item.definition.schedule_id}>
        <div className="recipe-card-heading"><div><strong>{item.definition.name}</strong><small>{t("schedules.summaryFriendly", { count: item.definition.repository_ids.length, plan: item.definition.target.plan.name, frequency: frequencyLabel })}</small></div><StatusBadge label={item.definition.state === ScheduleState.ACTIVE ? t("schedules.active") : t("schedules.paused")} tone={item.definition.state === ScheduleState.ACTIVE ? "success" : "neutral"} /></div>
        <p className="muted-text">{nextRun ? t("schedules.next", { time: nextRun }) : t("schedules.noNextRun")}</p>
        {missingOccurrence ? <p className="schedule-missing-occurrence" role="status">{t("schedules.missingOccurrence", { time: missingOccurrence })}</p> : null}
        {status ? <div className="schedule-latest"><span>{t("schedules.latestRun")}</span><StatusBadge label={t(`schedules.execution.${status}`)} tone={status === ScheduleExecutionStatus.COMPLETED ? "success" : status === ScheduleExecutionStatus.RUNNING ? "info" : "warning"} /></div> : null}
        {latest ? <dl className="summary-grid summary-grid--compact">
          <div><dt>{t("schedules.occurrence.expected")}</dt><dd><button className="text-button" type="button" onClick={() => inspect(item.definition, latest.schedule_run_id, ScheduleOccurrenceFilter.ALL)}>{latest.expected}</button></dd></div>
          <div><dt>{t("schedules.occurrence.successful")}</dt><dd>{latest.successful > 0 ? <button className="text-button" type="button" onClick={() => inspect(item.definition, latest.schedule_run_id, ScheduleOccurrenceFilter.SUCCESSFUL)}>{latest.successful}</button> : 0}</dd></div>
          <div><dt>{t("schedules.occurrence.failed")}</dt><dd>{latest.failed > 0 ? <button className="text-button" type="button" onClick={() => inspect(item.definition, latest.schedule_run_id, ScheduleOccurrenceFilter.FAILED)}>{latest.failed}</button> : 0}</dd></div>
          <div><dt>{t("schedules.occurrence.running")}</dt><dd>{latest.running > 0 ? <button className="text-button" type="button" onClick={() => inspect(item.definition, latest.schedule_run_id, ScheduleOccurrenceFilter.RUNNING)}>{latest.running}</button> : 0}</dd></div>
          <div><dt>{t("schedules.occurrence.pending")}</dt><dd>{latest.pending > 0 ? <button className="text-button" type="button" onClick={() => inspect(item.definition, latest.schedule_run_id, ScheduleOccurrenceFilter.PENDING)}>{latest.pending}</button> : 0}</dd></div>
          <div><dt>{t("schedules.occurrence.notStarted")}</dt><dd>{latest.not_started > 0 ? <button className="text-button" type="button" onClick={() => inspect(item.definition, latest.schedule_run_id, ScheduleOccurrenceFilter.NOT_STARTED)}>{latest.not_started}</button> : 0}</dd></div>
        </dl> : null}
        {latest?.status === ScheduleExecutionStatus.FAILED && latest.failure ? <p className="muted-text">{t("schedules.failure", { code: latest.failure.code, message: latest.failure.message })}</p> : null}
        <div className="schedule-card__primary-actions"><button className="button button--primary" type="button" disabled={actionsDisabled || isScheduleExecutionActive(status)} onClick={() => onRun(item.definition.schedule_id)}>{t("schedules.run")}</button>{latest ? <button className="button button--secondary" type="button" onClick={() => inspect(item.definition, latest.schedule_run_id, ScheduleOccurrenceFilter.ALL)}>{t("schedules.inspectOccurrence")}</button> : null}</div>
        {isScheduleExecutionActive(status) ? <small className="muted" role="status">{t("schedules.runBlockedExecution")}</small> : actionsDisabled ? <small className="muted" role="status">{t("schedules.runBlockedGlobal")}</small> : null}
        <details className="schedule-card__manage"><summary>{t("schedules.manage")}</summary><div className="schedule-card__manage-content">
          {editing ? <div className="form-grid schedule-edit-grid"><label><span>{t("schedules.edit.name")}</span><input value={draftName} onChange={(event) => setDraftName(event.target.value)} /></label><ScheduleFrequencyFields frequency={draftFrequency} customInterval={draftCustomInterval} frequencyLabelKey="schedules.edit.frequency" intervalLabelKey="schedules.edit.interval" onFrequencyChange={setDraftFrequency} onCustomIntervalChange={setDraftCustomInterval} /></div> : null}
          <div className="action-row">
            <button className="button button--secondary" type="button" disabled={actionsDisabled} onClick={() => onUpdate(item.definition.schedule_id, { ...editable(item.definition), state: toggledState(item.definition) })}>{item.definition.state === ScheduleState.ACTIVE ? t("schedules.disable") : t("schedules.enable")}</button>
            {editing ? <><button className="button button--secondary" type="button" disabled={actionsDisabled || !draftName.trim() || scheduleFrequencyMinutes(draftFrequency, draftCustomInterval) < 1 || scheduleFrequencyMinutes(draftFrequency, draftCustomInterval) > 10080} onClick={() => { const intervalMinutes = scheduleFrequencyMinutes(draftFrequency, draftCustomInterval); onUpdate(item.definition.schedule_id, { ...editable(item.definition), name: draftName.trim(), interval_minutes: intervalMinutes }); setEditingIdentity(null); }}>{t("schedules.edit.save")}</button><button className="text-button" type="button" onClick={() => setEditingIdentity(null)}>{t("common.cancel")}</button></> : <button className="text-button" type="button" onClick={() => { const preset = scheduleFrequencyPreset(item.definition.interval_minutes); setEditingIdentity(definitionIdentity); setDraftName(item.definition.name); setDraftFrequency(preset); setDraftCustomInterval(String(item.definition.interval_minutes)); }}>{t("schedules.edit.action")}</button>}
            {confirmingDelete ? <><button className="text-button text-button--danger" type="button" disabled={actionsDisabled} onClick={() => { onDelete(item.definition); setConfirmingDeleteIdentity(null); }}>{t("schedules.removeConfirm")}</button><button className="text-button" type="button" onClick={() => setConfirmingDeleteIdentity(null)}>{t("common.cancel")}</button></> : <button className="text-button text-button--danger" type="button" disabled={actionsDisabled} onClick={() => setConfirmingDeleteIdentity(definitionIdentity)}>{t("schedules.remove")}</button>}
          </div>
          <details className="advanced-disclosure"><summary>{t("schedules.technicalDetails")}</summary><code>{item.definition.schedule_id}</code></details>
        </div></details>
      </article>;
    })}</div>
    {occurrence ? <ScheduleOccurrenceInspector scheduleName={occurrence.scheduleName} page={occurrenceRows.data} loading={occurrenceRows.status === "read-loading" || occurrenceRows.status === "read-refreshing"} status={occurrenceFilter} offset={occurrenceOffset} onStatusChange={(filter) => selectOccurrencePopulation(filter)} onOffsetChange={setOccurrenceOffset} onOpenRun={onOpenRun} onClose={() => setOccurrence(null)} /> : null}
  </section>;
}
