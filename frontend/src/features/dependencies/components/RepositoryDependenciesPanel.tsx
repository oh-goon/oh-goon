import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { RepositoryDependencyPage } from "@generated/models";

interface Props {
  repositoryId: string;
  resource: ReadResource<RepositoryDependencyPage>;
  onBack: () => void;
  onPage: (offset: number) => void;
  onOpenPackage: (packageName: string) => void;
  onOpenEvidence: (runId: string, stepId: string) => void;
}

export function RepositoryDependenciesPanel({ repositoryId, resource, onBack, onPage, onOpenPackage, onOpenEvidence }: Props) {
  const { t } = useTranslation();
  const data = resource.data;
  const items = data?.items ?? [];
  return <section className="dependency-package-workspace">
    <button className="text-button dependency-back" type="button" onClick={onBack}>← {t("dependencies.backToSearch")}</button>
    <section className="panel dependency-population-panel">
      <div className="section-heading"><div><span className="eyebrow">{t("dependencies.repositoryEyebrow")}</span><h2>{data?.repository.name ?? repositoryId}</h2><p>{t("dependencies.repositoryDescription")}</p></div></div>
      {resource.status === "read-loading" ? <p className="muted loading-state" role="status">{t("dependencies.loading")}</p> : null}
      {resource.status === "read-error" ? <p className="notice notice--warning" role="alert">{resource.failure?.message}</p> : null}
      {data && items.length === 0 ? <p className="dependency-empty">{t("dependencies.noRepositoryEvidence")}</p> : null}
      {data ? <div className="dependency-repository-facts">{items.map((item) => <article className="dependency-repository-fact" key={item.package_name}>
        <header><button className="text-button" type="button" onClick={() => onOpenPackage(item.package_name)}>{item.package_name}</button><StatusBadge label={t(`dependencies.evidenceStates.${item.evidence.freshness}`)} tone={item.evidence.freshness === "current" ? "success" : "warning"} /></header>
        <div><span><b>{t("dependencies.resolved")}</b>{(item.resolved_versions ?? []).join(", ") || "—"}</span><span><b>{t("dependencies.contexts")}</b>{(item.contexts ?? []).join(", ")}</span><span><b>{t("dependencies.sources")}</b>{(item.sources ?? []).join(", ")}</span></div>
        <button className="button button--secondary button--small" type="button" onClick={() => onOpenEvidence(item.fleet_run_id, item.step_id)}>{t("dependencies.openEvidence")}</button>
      </article>)}</div> : null}
      {data && data.total > data.limit ? <nav className="dependency-pagination" aria-label={t("dependencies.repositoryPaginationLabel")}>
        <button className="button button--secondary" type="button" disabled={data.offset === 0} onClick={() => onPage(Math.max(0, data.offset - data.limit))}>{t("dependencies.previous")}</button>
        <span>{t("dependencies.pageRange", { from: data.offset + 1, to: Math.min(data.offset + items.length, data.total), total: data.total })}</span>
        <button className="button button--secondary" type="button" disabled={data.offset + data.limit >= data.total} onClick={() => onPage(data.offset + data.limit)}>{t("dependencies.next")}</button>
      </nav> : null}
    </section>
  </section>;
}
