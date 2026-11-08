import { useTranslation } from "react-i18next";

import { useFleetRunActions, useFleetRunStatus, useFleetSnapshot } from "@/app/FleetRunProvider";
import { ReadStateNotice } from "@/app/reads/ReadStateNotice";
import { PageHeading } from "@/components/common/PageHeading";
import { CatalogFeature } from "@/features/catalog/CatalogFeature";

interface Props {
  onOpenRun: (runId: string, repositoryId: string) => void;
  onOpenRepositories: () => void;
}

export function CatalogPage({ onOpenRun, onOpenRepositories }: Props) {
  const { t } = useTranslation();
  const fleet = useFleetSnapshot();
  const runStatus = useFleetRunStatus();
  const actions = useFleetRunActions();
  return <main className="page catalog-page">
    <PageHeading eyebrow={t("catalog.eyebrow")} title={t("catalog.title")} description={t("catalog.description")} />
    <ReadStateNotice resource={fleet.resource} onRefresh={() => void fleet.refresh()} />
    <CatalogFeature repositories={fleet.resource.data?.repositories ?? []} fleetFresh={fleet.resource.status === "read-fresh"} onOpenRun={onOpenRun} onOpenRepositories={onOpenRepositories} runStatus={runStatus} onGenerate={actions.runCatalog} onCancel={(runId) => void actions.cancelFleet(runId)} />
  </main>;
}
