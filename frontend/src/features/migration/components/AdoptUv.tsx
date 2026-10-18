import { useTranslation } from "react-i18next";

import { MigrationMode, MigrationReadiness, OperationAction, OperationTargetKind } from "@generated/enums";
import type { AdoptUvPreview, OperationTarget } from "@generated/models";
import { StatusBadge } from "@/components/common/StatusBadge";

interface Props {
  preview: AdoptUvPreview | null;
  actionsDisabled: boolean;
  onRun: (action: typeof OperationAction.ADOPT_UV, target: OperationTarget, repositoryIds: string[]) => void;
}

export function AdoptUv({ preview, actionsDisabled, onRun }: Props) {
  const { t } = useTranslation();
  const candidates = preview?.repositories ?? [];
  const safeIds = candidates.filter((item) => item.readiness === MigrationReadiness.SAFE_AUTOMATIC).map((item) => item.repository.repository_id);
  return (
    <section className="panel migration-panel" aria-label={t("migration.title")}>
      <div className="section-heading"><div>
        <span className="eyebrow">{t("migration.eyebrow")}</span><h2>{t("migration.title")}</h2><p>{t("migration.description")}</p>
      </div></div>
      <div className="compact-metrics">
        <span><strong>{preview?.already_uv ?? 0}</strong>{t("migration.alreadyUv")}</span>
        <span><strong>{preview?.safe_automatic ?? 0}</strong>{t("migration.safe")}</span>
        <span><strong>{preview?.review_required ?? 0}</strong>{t("migration.review")}</span>
        <span><strong>{preview?.unsupported ?? 0}</strong>{t("migration.unsupported")}</span>
      </div>
      <div className="action-row">
        <button className="button button--primary" type="button" disabled={actionsDisabled || safeIds.length === 0}
          onClick={() => onRun(OperationAction.ADOPT_UV, { kind: OperationTargetKind.MIGRATION, mode: MigrationMode.ADOPT }, safeIds)}>
          {t("migration.migrate", { count: safeIds.length })}
        </button>
      </div>
      <div className="compact-repository-list">
        {candidates.map((item) => <div className="compact-repository-row" key={item.repository.repository_id}>
          <div><strong>{item.repository.name}</strong><small>{(item.source_files ?? []).join(", ") || t("migration.noManifest")}</small></div>
          <div><StatusBadge label={item.source} tone="neutral" /><StatusBadge label={item.readiness} tone={item.readiness === MigrationReadiness.SAFE_AUTOMATIC ? "success" : item.readiness === MigrationReadiness.REVIEW_REQUIRED ? "warning" : "neutral"} /></div>
        </div>)}
      </div>
    </section>
  );
}
