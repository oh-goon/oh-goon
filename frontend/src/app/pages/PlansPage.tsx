import { useTranslation } from "react-i18next";

import { useMutationBusy } from "@/app/ApiClientProvider";
import { useFleetRunStatus } from "@/app/FleetRunProvider";
import { PageHeading } from "@/components/common/PageHeading";
import { SavedPlanManager } from "@/features/league/LeagueFeature";

export function PlansPage() {
  const { t } = useTranslation();
  const runStatus = useFleetRunStatus();
  const mutationBusy = useMutationBusy();
  const actionsDisabled = runStatus.activeRun !== null || mutationBusy;

  return <main className="page plans-page">
    <PageHeading eyebrow={t("plans.eyebrow")} title={t("plans.title")} description={t("plans.description")} />
    <SavedPlanManager actionsDisabled={actionsDisabled} />
  </main>;
}
