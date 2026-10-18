import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useMutationBusy } from "@/app/ApiClientProvider";
import { useFleetRunActions, useFleetRunStatus } from "@/app/FleetRunProvider";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import { CurrentRunPanel } from "@/features/maintenance/MaintenanceFeature";
import { useLeagueMap, useSharedPlans } from "@/features/league/LeagueFeature";
import {
  ScheduleCenter,
  ScheduleCreatePanel,
  useCreateScheduleFromSharedPlan,
  useDeleteSchedule,
  useRunSchedule,
  useScheduleCatalog,
  useUpdateSchedule,
} from "@/features/schedules/SchedulesFeature";

interface Props {
  onOpenRun: (runId: string) => void;
  onOpenRepositories: () => void;
  onOpenPlans: () => void;
}

export function SchedulesPage({ onOpenRun, onOpenRepositories, onOpenPlans }: Props) {
  const { t } = useTranslation();
  const runStatus = useFleetRunStatus();
  const runActions = useFleetRunActions();
  const mutationBusy = useMutationBusy();
  const schedules = useScheduleCatalog(true);
  const league = useLeagueMap();
  const plans = useSharedPlans();
  const createSchedule = useCreateScheduleFromSharedPlan();
  const updateSchedule = useUpdateSchedule();
  const deleteSchedule = useDeleteSchedule();
  const runSchedule = useRunSchedule(schedules.resource.data);
  const [createOpen, setCreateOpen] = useState(false);
  const baseDisabled = runStatus.activeRun !== null || mutationBusy;
  const scheduleAuthorityFresh = schedules.resource.status === "read-fresh";
  const createAuthorityFresh = league.resource.status === "read-fresh" && plans.resource.status === "read-fresh";
  const scheduleActionsDisabled = baseDisabled || !scheduleAuthorityFresh;
  const createActionsDisabled = baseDisabled || !createAuthorityFresh;
  const activeRunId = runStatus.activeRun?.fleet_run_id ?? null;
  const scheduleCount = schedules.resource.data?.schedules?.length ?? null;
  const error = schedules.resource.status === "read-error"
    ? schedules.resource.failure?.message ?? null
    : league.resource.status === "read-error"
      ? league.resource.failure?.message ?? null
      : plans.resource.status === "read-error"
        ? plans.resource.failure?.message ?? null
        : runStatus.activeRunError;

  useEffect(() => {
    if (scheduleCount === 0) setCreateOpen(true);
  }, [scheduleCount]);

  return <main className="page">
    <PageHeading
      eyebrow={t("schedulesPage.eyebrow")}
      title={t("schedulesPage.title")}
      description={t("schedulesPage.description")}
      action={scheduleCount !== 0 ? <button className="button button--primary" type="button" aria-expanded={createOpen} onClick={() => setCreateOpen((value) => !value)}>{createOpen ? t("schedulesPage.closeCreate") : t("schedulesPage.newSchedule")}</button> : undefined}
    />
    {error ? <ErrorBanner message={error} /> : null}
    {runStatus.activeRun ? <CurrentRunPanel
      run={runStatus.activeRun}
      mode="active"
      detail={runStatus.activeRunDetail}
      onCancel={(runId) => void runActions.cancelFleet(runId)}
      onInspect={() => { if (activeRunId) onOpenRun(activeRunId); }}
    /> : null}
    {createOpen ? <ScheduleCreatePanel
      league={league.resource.data}
      plans={plans.resource.data}
      actionsDisabled={createActionsDisabled}
      onOpenRepositories={onOpenRepositories}
      onOpenPlans={onOpenPlans}
      onCreate={async (id, request) => {
        const result = await createSchedule.create(id, request);
        return result.outcome === "saved";
      }}
      onCreated={() => setCreateOpen(false)}
    /> : null}
    <ScheduleCenter
      catalog={schedules.resource.data}
      loading={schedules.resource.status === "read-loading" || schedules.resource.status === "read-refreshing"}
      league={league.resource.data}
      actionsDisabled={scheduleActionsDisabled}
      onUpdate={(id, schedule) => { void updateSchedule.update(id, schedule); }}
      onDelete={(definition) => { void deleteSchedule.remove(definition); }}
      onRun={(id) => { void runSchedule.run(id); }}
      onOpenRun={onOpenRun}
    />
  </main>;
}
