import { useTranslation } from "react-i18next";

import { RecoveryPanel } from "@/components/common/RecoveryPanel";
import type { ReadResource } from "@/domain/reads/ReadResource";

interface Props {
  resource: ReadResource<unknown>;
  onRefresh?: () => void;
}

export function ReadStateNotice({ resource, onRefresh }: Props) {
  const { t } = useTranslation();
  if (resource.status === "read-refreshing") {
    return <div className="notice notice--scope read-state-refreshing" role="status">{t("readState.refreshing")}</div>;
  }
  if (resource.status !== "read-stale") return null;
  return <RecoveryPanel
    tone="warning"
    live="polite"
    title={t("readState.staleTitle")}
    message={t("readState.staleMessage", { time: formatTime(resource.fetchedAt) })}
    actions={onRefresh ? <button className="button button--secondary" type="button" onClick={onRefresh}>{t("app.refresh")}</button> : null}
  />;
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
