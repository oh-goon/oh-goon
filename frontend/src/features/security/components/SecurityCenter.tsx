import { useTranslation } from "react-i18next";

import { OperationTargetKind, UvRecipeId } from "@generated/enums";
import type { FleetSecuritySummary, OperationTarget } from "@generated/models";
import { StatusBadge } from "@/components/common/StatusBadge";

interface Props {
  security: FleetSecuritySummary | null;
  actionsDisabled: boolean;
  onRunRecipe: (target: OperationTarget, repositoryIds: string[]) => void;
  onExplorePackage: (packageName: string) => void;
  sbomRunId: string | null;
}

export function SecurityCenter({ security, actionsDisabled, onRunRecipe, onExplorePackage, sbomRunId }: Props) {
  const { t } = useTranslation();
  const findings = security?.findings ?? [];
  const repositoryIds = security?.repository_ids ?? [];
  return (
    <section className="panel security-panel" aria-label={t("security.title")}>
      <div className="section-heading"><div>
        <span className="eyebrow">{t("security.eyebrow")}</span><h2>{t("security.title")}</h2><p>{t("security.description")}</p>
      </div></div>
      <div className="compact-metrics">
        <span><strong>{security?.audited ?? 0}</strong>{t("security.audited")}</span>
        <span><strong>{security?.clean ?? 0}</strong>{t("security.clean")}</span>
        <span><strong>{security?.affected_repositories ?? 0}</strong>{t("security.affected")}</span>
      </div>
      <div className="action-row">
        <button className="button button--primary" type="button" disabled={actionsDisabled || (security?.repositories ?? 0) === 0}
          onClick={() => onRunRecipe({ kind: OperationTargetKind.RECIPE, recipe: UvRecipeId.AUDIT, args: [] }, repositoryIds)}>{t("security.audit")}</button>
        <button className="button button--secondary" type="button" disabled={actionsDisabled || (security?.repositories ?? 0) === 0}
          onClick={() => onRunRecipe({ kind: OperationTargetKind.RECIPE, recipe: UvRecipeId.SBOM, args: [] }, repositoryIds)}>{t("security.sbom")}</button>
        {sbomRunId ? <a className="button button--secondary" href={`/api/fleet/security/sbom/${encodeURIComponent(sbomRunId)}`} download>{t("security.downloadSbom")}</a> : null}
      </div>
      {findings.length === 0 ? <div className="attention-clear"><strong>{t("security.noFindings")}</strong></div> : (
        <div className="security-findings">
          {findings.map((finding) => {
            const ids = (finding.repositories ?? []).map((repository) => repository.repository_id);
            return <article className="security-finding" key={`${finding.advisory_id}:${finding.package ?? ""}`}>
              <div><strong>{finding.advisory_id}</strong>{finding.package ? <StatusBadge label={finding.package} tone="danger" /> : null}</div>
              {finding.summary ? <p>{finding.summary}</p> : null}
              <small>{t("security.repositories", { count: ids.length })}{(finding.fixed_versions ?? []).length ? ` · ${t("security.fixed", { versions: (finding.fixed_versions ?? []).join(", ") })}` : ""}</small>
              {finding.package ? <button className="button button--secondary" type="button" disabled={actionsDisabled}
                onClick={() => onExplorePackage(finding.package!)}>{t("security.exploreDependency")}</button> : null}
            </article>;
          })}
        </div>
      )}
    </section>
  );
}
