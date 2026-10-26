import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useMutationBusy } from "@/app/ApiClientProvider";
import { queryParamsFromHash, writeAppLocation } from "@/app/navigation";
import { useFleetRunActions, useFleetRunStatus } from "@/app/FleetRunProvider";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import { SharedPlanPanel, useLeagueMap } from "@/features/league/LeagueFeature";
import { CurrentRunPanel } from "@/features/maintenance/MaintenanceFeature";
import { useDependencyTargetSelection } from "@/features/dependencies/DependenciesFeature";

interface Props {
  onOpenRun: (runId: string, repositoryId?: string) => void;
  onOpenPlans: () => void;
}

export function SharedRunsPage({ onOpenRun, onOpenPlans }: Props) {
  const { t } = useTranslation();
  const runStatus = useFleetRunStatus();
  const activeRun = runStatus.activeRun;
  const [submittedSharedRunId, setSubmittedSharedRunId] = useState<string | null>(null);
  const runActions = useFleetRunActions();
  const mutationBusy = useMutationBusy();
  const league = useLeagueMap();
  const selectionId = queryParamsFromHash().get("selection") ?? "";
  const selection = useDependencyTargetSelection(selectionId);
  const activeRunId = activeRun?.fleet_run_id ?? null;
  const actionsDisabled = activeRunId !== null || mutationBusy;
  const ownsCompletedRun = activeRun === null
    && submittedSharedRunId !== null
    && runStatus.completedLocalRun?.fleet_run_id === submittedSharedRunId;
  const error = league.resource.status === "read-error"
    ? league.resource.failure?.message ?? null
    : selection.status === "read-error"
      ? selection.failure?.message ?? null
      : runStatus.activeRunError ?? (ownsCompletedRun ? runStatus.completedLocalRunError : null);

  return <main className="page">
    <PageHeading eyebrow={t("sharedRuns.eyebrow")} title={t("sharedRuns.title")} description={t("sharedRuns.description")} />
    {error ? <ErrorBanner message={error} /> : null}
    {selection.data ? <div className="notice notice--scope shared-run-observed-selection" role="status">
      <strong>{t("sharedPlans.observedSelectionTitle", { count: selection.data.total })}</strong>
      <p>{t("sharedPlans.observedSelectionMessage", { packageName: selection.data.filter.package_name })}</p>
      <dl className="shared-run-observed-selection__facts">
        <div><dt>{t("sharedPlans.observedSelectionPackage")}</dt><dd>{selection.data.filter.package_name}</dd></div>
        <div><dt>{t("sharedPlans.observedSelectionCoverage")}</dt><dd>{t(`dependencies.coverageStates.${selection.data.filter.coverage ?? "matching"}`)}</dd></div>
        {selection.data.filter.advisory_id ? <div><dt>{t("sharedPlans.observedSelectionAdvisory")}</dt><dd>{selection.data.filter.advisory_id}</dd></div> : null}
        {selection.data.filter.version_specifier ? <div><dt>{t("sharedPlans.observedSelectionRange")}</dt><dd><code>{selection.data.filter.version_specifier}</code></dd></div> : null}
        {selection.data.filter.resolved_version ? <div><dt>{t("sharedPlans.observedSelectionVersion")}</dt><dd>{selection.data.filter.resolved_version}</dd></div> : null}
        {selection.data.filter.evidence && selection.data.filter.evidence !== "all" ? <div><dt>{t("sharedPlans.observedSelectionEvidence")}</dt><dd>{t(`dependencies.evidenceStates.${selection.data.filter.evidence}`)}</dd></div> : null}
      </dl>
      <small>{t("sharedPlans.observedSelectionImmutable")}</small>
    </div> : null}
    {activeRun ? <CurrentRunPanel
      run={activeRun}
      mode="active"
      detail={runStatus.activeRunDetail}
      onCancel={(runId) => void runActions.cancelFleet(runId)}
      onInspect={() => onOpenRun(activeRun.fleet_run_id)}
      onInspectRepository={(repositoryId) => onOpenRun(activeRun.fleet_run_id, repositoryId)}
    /> : null}
    {ownsCompletedRun && submittedSharedRunId && runStatus.completedLocalRun ? <CurrentRunPanel
      run={runStatus.completedLocalRun}
      mode="completed"
      detail={runStatus.completedLocalRunDetail}
      onInspect={() => onOpenRun(submittedSharedRunId)}
      onInspectRepository={(repositoryId) => onOpenRun(submittedSharedRunId, repositoryId)}
    /> : null}
    <SharedPlanPanel
      key={selection.data?.query_id ?? "shared-run"}
      league={league.resource.data}
      actionsDisabled={actionsDisabled}
      onRun={(planKey, repositoryIds, expectedPlanId, expectedPlanRevision, expectedExecutionDigest, repositoryInstanceIds, inputs, agent) =>
        runActions.runSharedPlan(planKey, repositoryIds, expectedPlanId, expectedPlanRevision, expectedExecutionDigest, repositoryInstanceIds, inputs, agent)}
      onAcceptedRun={setSubmittedSharedRunId}
      initialRepositoryIds={selection.data?.repo_ids ?? []}
      initialRepositoryInstanceIds={selection.data?.repository_instance_ids}
      onOpenPlans={onOpenPlans}
      onOpenCampaign={(campaignId) => writeAppLocation("push", "runs", { campaign: campaignId })}
    />
  </main>;
}
