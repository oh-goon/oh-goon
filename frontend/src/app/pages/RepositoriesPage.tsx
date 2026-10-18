import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useMutationBusy } from "@/app/ApiClientProvider";
import { useFleetRunActions, useFleetRunStatus, useFleetSnapshot } from "@/app/FleetRunProvider";
import type { ExperienceMode } from "@/app/preferences";
import { ReadStateNotice } from "@/app/reads/ReadStateNotice";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import { FleetRepositoryScope } from "@generated/enums";
import { LeagueMapPanel, useLeagueMap } from "@/features/league/LeagueFeature";
import { CurrentRunPanel } from "@/features/maintenance/MaintenanceFeature";
import { FleetRepositorySummary } from "@/features/overview/OverviewFeature";
import { ExclusionNotice, RepositoryTable, useRepositoryExclusion } from "@/features/repositories/RepositoriesFeature";
import { FirstRunOnboarding, useLocations } from "@/features/locations/LocationsFeature";

interface Props {
  experience: ExperienceMode;
  onOpenSettings: () => void;
  onOpenHelp: () => void;
  onOpenRuns: (repositoryId?: string) => void;
  onOpenRun: (runId: string, repositoryId?: string) => void;
  onOpenDependencies: (repositoryId?: string) => void;
}

export function RepositoriesPage({ experience, onOpenSettings, onOpenHelp, onOpenRuns, onOpenRun, onOpenDependencies }: Props) {
  const { t } = useTranslation();
  const fleet = useFleetSnapshot();
  const locations = useLocations();
  const exclusion = useRepositoryExclusion();
  const runStatus = useFleetRunStatus();
  const activeRun = runStatus.activeRun;
  const runActions = useFleetRunActions();
  const league = useLeagueMap();
  const mutationBusy = useMutationBusy();
  const [submittedRepositoryRunId, setSubmittedRepositoryRunId] = useState<string | null>(null);
  const snapshot = fleet.resource.data;
  const repositories = snapshot?.repositories ?? [];
  const unavailableLocations = locations.resource.data?.unavailable ?? [];
  const includedCount = useMemo(
    () => repositories.filter((repo) => repo.scope === FleetRepositoryScope.INCLUDED).length,
    [repositories],
  );
  const actionsDisabled = activeRun !== null || mutationBusy;
  const repositoryActionsDisabled = actionsDisabled || fleet.resource.status !== "read-fresh";
  const ownsCompletedRun = activeRun === null
    && submittedRepositoryRunId !== null
    && runStatus.completedLocalRun?.fleet_run_id === submittedRepositoryRunId;
  const activeRepositoryId = activeRun?.targets.length === 1
    ? activeRun.targets[0]?.repository_id ?? null
    : null;
  const error = fleet.resource.status === "read-error"
    ? fleet.resource.failure?.message ?? null
    : locations.resource.status === "read-error"
      ? locations.resource.failure?.message ?? null
      : league.resource.status === "read-error"
        ? league.resource.failure?.message ?? null
        : runStatus.activeRunError ?? (ownsCompletedRun ? runStatus.completedLocalRunError : null);

  if (locations.resource.data !== null && locations.resource.data.locations.length === 0) {
    return <main className="page page--narrow">
      <FirstRunOnboarding locations={[]} revision={locations.resource.data.revision} actionsDisabled={actionsDisabled} onHelp={onOpenHelp} />
    </main>;
  }

  const repositoryInventory = <section className={experience === "advanced" ? "panel repository-panel" : "repository-inventory-content"}>
    <div className="section-heading"><div><span className="eyebrow">{t("repositoriesPage.inventoryEyebrow")}</span><h2>{t("repositoriesPage.inventoryTitle")}</h2><p>{t("repositoriesPage.inventoryDescription")}</p></div></div>
    <FleetRepositorySummary summary={snapshot?.summary ?? null} selectedCount={includedCount} />
    <ExclusionNotice excluded={snapshot?.summary?.excluded ?? 0} />
    {fleet.resource.status === "read-loading" ? <p>{t("fleet.loading")}</p> : null}
    {fleet.resource.status !== "read-loading" && repositories.length === 0 ? <EmptyState title={t("fleet.emptyTitle")} message={t("fleet.emptyMessage")} /> : null}
    {repositories.length > 0 ? <RepositoryTable repositories={repositories} actionsDisabled={repositoryActionsDisabled} advanced={experience === "advanced"} onSetExcluded={(repo, repositoryInstanceId, excluded) => void exclusion.setExcluded(repo, repositoryInstanceId, excluded)} onOpenRuns={(repositoryId) => onOpenRuns(repositoryId)} onOpenDependencies={onOpenDependencies} /> : null}
  </section>;

  return <main className="page">
    <PageHeading eyebrow={t("repositoriesPage.eyebrow")} title={t("repositoriesPage.title")} description={t("repositoriesPage.description")} action={<div className="button-row"><button className="button button--secondary" type="button" onClick={() => onOpenDependencies()}>{t("repositoriesPage.exploreDependencies")}</button><button className="button button--secondary" type="button" onClick={onOpenSettings}>{t("repositoriesPage.manageFolders")}</button></div>} />
    {error ? <ErrorBanner message={error} /> : null}
    {unavailableLocations.length > 0 ? <section className="notice notice--warning" role="status"><strong>{t("locations.unavailableTitle")}</strong><p>{t("locations.unavailableMessage", { count: unavailableLocations.length })}</p><ul>{unavailableLocations.map((location) => <li key={location}><code>{location}</code></li>)}</ul></section> : null}
    <ReadStateNotice resource={fleet.resource} onRefresh={() => void fleet.refresh()} />
    <section className="repository-cicd-workspace">
      {activeRun ? <CurrentRunPanel
        run={activeRun}
        mode="active"
        detail={runStatus.activeRunDetail}
        onCancel={(runId) => void runActions.cancelFleet(runId)}
        onInspect={() => onOpenRun(activeRun.fleet_run_id)}
        onInspectRepository={(repositoryId) => onOpenRun(activeRun.fleet_run_id, repositoryId)}
      /> : null}
      <LeagueMapPanel
        league={league.resource.data}
        loading={league.resource.status === "read-loading"}
        onRefresh={() => void league.refresh()}
        onRunTeam={(repository, expectedPipelineSha256, expectedRepositoryInstanceId) => void runActions.runTeam(repository, expectedPipelineSha256, expectedRepositoryInstanceId).then((result) => {
          if (result.outcome === "saved") setSubmittedRepositoryRunId(result.value.fleet_run_id);
        })}
        actionsDisabled={actionsDisabled || league.resource.status !== "read-fresh"}
        activeRepositoryId={activeRepositoryId}
        advanced={experience === "advanced"}
        showSummary={false}
      />
      {ownsCompletedRun && submittedRepositoryRunId && runStatus.completedLocalRun ? <CurrentRunPanel
        run={runStatus.completedLocalRun}
        mode="completed"
        detail={runStatus.completedLocalRunDetail}
        onInspect={() => onOpenRun(submittedRepositoryRunId)}
        onInspectRepository={(repositoryId) => onOpenRun(submittedRepositoryRunId, repositoryId)}
      /> : null}
    </section>
    {experience === "advanced" ? repositoryInventory : <details className="panel repository-panel repository-inventory-disclosure">
      <summary>{t("repositoriesPage.manageInventory")}</summary>
      {repositoryInventory}
    </details>}
  </main>;
}