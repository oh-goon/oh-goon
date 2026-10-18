import { useTranslation } from "react-i18next";

import { appDiagnosticStore } from "@/domain/diagnostics/appDiagnosticStore";
import { useAppDiagnostics } from "@/app/diagnostics/useAppDiagnostics";
import { EmptyState } from "@/components/common/EmptyState";

export function AppDiagnosticsPanel() {
  const { t } = useTranslation();
  const diagnostics = useAppDiagnostics();

  return (
    <section className="panel diagnostics-panel">
      <div className="section-heading">
        <div><span className="eyebrow">{t("diagnostics.eyebrow")}</span><h2>{t("diagnostics.title")}</h2><p>{t("diagnostics.description")}</p></div>
        {diagnostics.length > 0 ? <button className="button button--secondary" type="button" onClick={() => appDiagnosticStore.clear()}>{t("diagnostics.clear")}</button> : null}
      </div>
      {diagnostics.length === 0 ? <EmptyState title={t("diagnostics.emptyTitle")} message={t("diagnostics.emptyMessage")} /> : (
        <div className="diagnostic-list">
          {diagnostics.map((diagnostic) => (
            <article className="diagnostic-row" key={diagnostic.id}>
              <div><strong>{diagnostic.code}</strong><small>{diagnostic.source}</small></div>
              <p>{diagnostic.message}</p>
              {diagnostic.scope !== null ? <small>{t("diagnostics.scope", { scope: diagnostic.scope })}</small> : null}
              {diagnostic.requestId !== null ? <small>{t("diagnostics.requestId", { requestId: diagnostic.requestId })}</small> : null}
              {diagnostic.causalityRevision !== null ? <small>{t("diagnostics.causalityRevision", { revision: diagnostic.causalityRevision })}</small> : null}
              <small>{t("diagnostics.occurrences", { count: diagnostic.occurrences })}</small>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
