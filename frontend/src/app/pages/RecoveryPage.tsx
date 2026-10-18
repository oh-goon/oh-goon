import { useTranslation } from "react-i18next";

import { useMutationBusy } from "@/app/ApiClientProvider";
import { ReadStateNotice } from "@/app/reads/ReadStateNotice";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import { HockeyPenaltyBoxNotice } from "@/features/hockey/HockeyTeamFeature";
import { RecoveryCenter, useRecoveryActions, useRecoveryStatus } from "@/features/recovery/RecoveryFeature";

export function RecoveryPage() {
  const { t } = useTranslation();
  const recovery = useRecoveryStatus();
  const actions = useRecoveryActions();
  const busy = useMutationBusy();
  const error = recovery.resource.status === "read-error" ? recovery.resource.failure?.message ?? null : null;
  return <main className="page page--recovery">
    <PageHeading eyebrow={t("recovery.eyebrow")} title={t("recovery.title")} description={t("recovery.description")} />
    {error ? <ErrorBanner message={error} /> : null}
    <ReadStateNotice resource={recovery.resource} onRefresh={() => void recovery.refresh()} />
    <HockeyPenaltyBoxNotice status={recovery.resource.data} />
    <RecoveryCenter
      status={recovery.resource.data}
      actionsDisabled={busy || recovery.resource.status !== "read-fresh"}
      onReconcile={(operationId, recordVersion) => void actions.reconcile(operationId, recordVersion)}
      onAcknowledge={(operationId, recordVersion) => void actions.acknowledge(operationId, t("recovery.manualInspectionNote"), recordVersion)}
    />
  </main>;
}
