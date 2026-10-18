import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useMutationBusy } from "@/app/ApiClientProvider";
import { useFleetRunActions, useFleetRunStatus } from "@/app/FleetRunProvider";
import type { ExperienceMode } from "@/app/preferences";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import { OperationAction, UvRecipeId } from "@generated/enums";
import { FleetCommandCenter, useFleetCommands } from "@/features/commands/CommandsFeature";
import { HockeyPersonaSpotlight } from "@/features/hockey/HockeyTeamFeature";
import { AdoptUv, useAdoptUvPreview } from "@/features/migration/MigrationFeature";
import { ExecutorOperations, useExecutorOperations } from "@/features/operations/OperationsFeature";
import { SecurityCenter, useFleetSecurity } from "@/features/security/SecurityFeature";

type ToolId = "security" | "commands" | "migration" | "operations";
const STANDARD_TOOLS: ToolId[] = ["security"];
const ADVANCED_TOOLS: ToolId[] = ["commands", "migration", "operations"];

interface Props {
  experience: ExperienceMode;
  onOpenSettings: () => void;
  onOpenDependencies: (packageName: string) => void;
}

export function ToolsPage({ experience, onOpenSettings, onOpenDependencies }: Props) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<ToolId | null>(null);
  const runStatus = useFleetRunStatus();
  const runActions = useFleetRunActions();
  const mutationBusy = useMutationBusy();
  const commandsEnabled = selected === "commands" || selected === "security";
  const commands = useFleetCommands(commandsEnabled);
  const security = useFleetSecurity(selected === "security");
  const migration = useAdoptUvPreview(selected === "migration");
  const executorOperations = useExecutorOperations(selected === "operations");
  const allowed = experience === "advanced" ? [...STANDARD_TOOLS, ...ADVANCED_TOOLS] : STANDARD_TOOLS;
  const baseDisabled = runStatus.activeRun !== null || mutationBusy;
  const selectedAuthorityFresh = selected === "security"
    ? security.resource.status === "read-fresh" && commands.resource.status === "read-fresh"
    : selected === "commands"
      ? commands.resource.status === "read-fresh"
      : selected === "migration"
        ? migration.resource.status === "read-fresh"
        : selected === "operations"
          ? executorOperations.executors.status === "read-fresh" && executorOperations.placementQueue.status === "read-fresh"
          : true;
  const disabled = baseDisabled || !selectedAuthorityFresh;
  const resources = [commands.resource, security.resource, migration.resource, executorOperations.executors, executorOperations.placementQueue];
  const error = resources.find((resource) => resource.status === "read-error")?.failure?.message ?? runStatus.activeRunError;

  return <main className="page"><PageHeading eyebrow={t("tools.eyebrow")} title={t("tools.title")} description={t("tools.description")} />
    {error ? <ErrorBanner message={error} /> : null}
    {!selected ? <section className="tool-picker">{allowed.map((tool) => <button className="tool-picker-card" type="button" key={tool} onClick={() => setSelected(tool)}><strong>{t(`tools.items.${tool}.title`)}</strong><span>{t(`tools.items.${tool}.description`)}</span><small>{t("tools.open")}</small></button>)}{experience === "standard" ? <button className="tool-picker-card tool-picker-card--advanced" type="button" onClick={onOpenSettings}><strong>{t("tools.advancedTitle")}</strong><span>{t("tools.advancedMessage")}</span><small>{t("tools.enableAdvanced")}</small></button> : null}</section> : <><button className="text-button tool-back" type="button" onClick={() => setSelected(null)}>← {t("tools.back")}</button>
      {selected === "security" ? <><HockeyPersonaSpotlight persona="redTeam" /><SecurityCenter security={security.resource.data} actionsDisabled={disabled} onRunRecipe={(target, repositoryIds) => void runActions.runFleet(OperationAction.RECIPE, { target, repositoryIds, repositoryInstanceIds: security.resource.data?.repository_instance_ids ?? {} })} onExplorePackage={onOpenDependencies} sbomRunId={commands.resource.data?.recipes.find((row) => row.spec.recipe === UvRecipeId.SBOM)?.latest_run_id ?? null} /></> : null}
      {selected === "commands" ? <FleetCommandCenter center={commands.resource.data} actionsDisabled={disabled} onRun={(target, repositoryIds) => void runActions.runFleet(OperationAction.RECIPE, { target, repositoryIds, repositoryInstanceIds: commands.resource.data?.repository_instance_ids ?? {} })} /> : null}
      {selected === "migration" ? <AdoptUv preview={migration.resource.data} actionsDisabled={disabled} onRun={(action, target, repositoryIds) => void runActions.runFleet(action, { target, repositoryIds, repositoryInstanceIds: Object.fromEntries((migration.resource.data?.repositories ?? []).filter((item) => repositoryIds.includes(item.repository.repository_id)).map((item) => [item.repository.repository_id, item.repository_instance_id])) })} /> : null}
      {selected === "operations" ? <ExecutorOperations executors={executorOperations.executors.data} placementQueue={executorOperations.placementQueue.data} actionsDisabled={disabled} onDrain={(executor) => void executorOperations.drain(executor)} onActivate={(executor) => void executorOperations.activate(executor)} onRevokeTrust={(executor) => void executorOperations.revokeTrust(executor)} onRestoreTrust={(executor) => void executorOperations.restoreTrust(executor)} onCancelQueued={(entry) => void executorOperations.cancelQueued(entry)} onRefresh={() => void executorOperations.refresh()} /> : null}
    </>}
  </main>;
}
