import { useState } from "react";

import { AppShell } from "@/app/AppShell";
import { useReadController } from "@/app/ApiClientProvider";
import { ComponentErrorBoundary } from "@/app/errors/ComponentErrorBoundary";
import { useAppNavigation } from "@/app/navigation";
import { OverviewPage } from "@/app/pages/OverviewPage";
import { PlansPage } from "@/app/pages/PlansPage";
import { HelpPage } from "@/app/pages/HelpPage";
import { FailuresPage } from "@/app/pages/FailuresPage";
import { DependenciesPage } from "@/app/pages/DependenciesPage";
import { RepositoriesPage } from "@/app/pages/RepositoriesPage";
import { RecoveryPage } from "@/app/pages/RecoveryPage";
import { RunsPage } from "@/app/pages/RunsPage";
import { SchedulesPage } from "@/app/pages/SchedulesPage";
import { ScoutPage } from "@/app/pages/ScoutPage";
import { SettingsPage } from "@/app/pages/SettingsPage";
import { SharedRunsPage } from "@/app/pages/SharedRunsPage";
import { ToolsPage } from "@/app/pages/ToolsPage";
import { loadExperienceMode, saveExperienceMode, type ExperienceMode } from "@/app/preferences";
import { READ_SCOPES } from "@/domain/reads/readResources";

export function App() {
  const { page, navigate } = useAppNavigation();
  const [experience, setExperience] = useState<ExperienceMode>(() => loadExperienceMode());
  const reads = useReadController();
  const changeExperience = (mode: ExperienceMode) => { saveExperienceMode(mode); setExperience(mode); };
  const refreshAll = () => reads.invalidateMany(READ_SCOPES);

  return <AppShell page={page} experience={experience} onNavigate={navigate} onRefresh={refreshAll}>
    <ComponentErrorBoundary key={page} area={page}>
      {page === "overview" ? <OverviewPage navigate={navigate} /> : null}
      {page === "failures" ? <FailuresPage navigate={navigate} /> : null}
      {page === "repositories" ? <RepositoriesPage experience={experience} onOpenSettings={() => navigate("settings")} onOpenHelp={() => navigate("help")} onOpenRuns={(repositoryId) => navigate("runs", repositoryId ? { repository: repositoryId } : undefined)} onOpenRun={(runId, repositoryId) => navigate("runs", { run: runId, ...(repositoryId ? { repository: repositoryId } : {}) })} onOpenDependencies={(repositoryId) => navigate("dependencies", repositoryId ? { repository: repositoryId } : undefined)} /> : null}
      {page === "dependencies" ? <DependenciesPage navigate={navigate} /> : null}
      {page === "plans" ? <PlansPage /> : null}
      {page === "shared-runs" ? <SharedRunsPage onOpenPlans={() => navigate("plans")} onOpenRun={(runId, repositoryId) => navigate("runs", { run: runId, ...(repositoryId ? { repository: repositoryId } : {}) })} /> : null}
      {page === "schedules" ? <SchedulesPage onOpenRun={(runId) => navigate("runs", { run: runId })} onOpenRepositories={() => navigate("repositories")} onOpenPlans={() => navigate("plans")} /> : null}
      {page === "runs" ? <RunsPage onOpenRecovery={() => navigate("recovery")} onOpenRepositories={() => navigate("repositories")} onOpenSharedRuns={() => navigate("shared-runs")} /> : null}
      {page === "scout" ? <ScoutPage navigate={navigate} /> : null}
      {page === "recovery" ? <RecoveryPage /> : null}
      {page === "tools" ? <ToolsPage experience={experience} onOpenSettings={() => navigate("settings")} onOpenDependencies={(packageName) => navigate("dependencies", { q: packageName, package: packageName })} /> : null}
      {page === "settings" ? <SettingsPage experience={experience} onExperienceChange={changeExperience} onOpenRepositories={() => navigate("repositories")} /> : null}
      {page === "help" ? <HelpPage onRepositories={() => navigate("repositories")} /> : null}
    </ComponentErrorBoundary>
  </AppShell>;
}
