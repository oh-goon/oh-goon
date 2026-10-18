import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { FleetRepositoryScope, ReadinessState } from "@generated/enums";
import type { FleetRepository } from "@generated/models";

interface Props {
  repositories: FleetRepository[];
  actionsDisabled: boolean;
  advanced: boolean;
  onSetExcluded(repository: FleetRepository, repositoryInstanceId: string, excluded: boolean): void;
  onOpenRuns(repositoryId: string): void;
  onOpenDependencies(repositoryId: string): void;
}

type Filter = "all" | "attention" | "ready";
const PAGE_SIZE = 50;

export function RepositoryTable({ repositories, actionsDisabled, advanced, onSetExcluded, onOpenRuns, onOpenDependencies }: Props) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [page, setPage] = useState(0);
  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return repositories.filter((entry) => {
      if (normalized && !`${entry.repository.name} ${entry.repository.path}`.toLowerCase().includes(normalized)) return false;
      if (filter === "attention") return entry.needs_attention;
      if (filter === "ready") return entry.scope === FleetRepositoryScope.INCLUDED && entry.readiness_state === ReadinessState.READY;
      return true;
    });
  }, [filter, query, repositories]);
  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageItems = visible.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);
  const from = visible.length === 0 ? 0 : safePage * PAGE_SIZE + 1;
  const to = Math.min(visible.length, (safePage + 1) * PAGE_SIZE);

  return <>
    <div className="repository-toolbar">
      <div className="repository-filters">
        <input aria-label={t("repositories.search")} placeholder={t("repositories.search")} value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }} />
        {(["all", "attention", "ready"] as const).map((value) => <button key={value} className={filter === value ? "button button--primary button--small" : "button button--secondary button--small"} type="button" onClick={() => { setFilter(value); setPage(0); }}>{t(`repositories.filters.${value}`)}</button>)}
        <span className="repository-visible-count">{t("repositories.visibleCount", { visible: visible.length, total: repositories.length })}</span>
      </div>
    </div>
    <div className="table-shell"><table><thead><tr>
      <th>{t("repositories.columns.repository")}</th>
      {advanced ? <th>{t("repositories.columns.readiness")}</th> : null}
      <th>{t("repositories.columns.scope")}</th>
      <th>{t("repositories.columns.action")}</th>
    </tr></thead><tbody>{pageItems.map((repo) => {
      const excluded = repo.scope === FleetRepositoryScope.EXCLUDED;
      return <tr key={repo.repository.repository_id} className={excluded ? "repository-row--excluded" : undefined}>
        <td><strong>{repo.repository.name}</strong><small>{repo.repository.path}</small></td>
        {advanced ? <td>{t(`repositories.readiness.${repo.readiness_state}`)}</td> : null}
        <td>{t(`repositories.scope.${excluded ? "excluded" : "included"}`)}</td>
        <td><div className="button-row">
          <button className="button button--secondary button--small" type="button" disabled={actionsDisabled} onClick={() => onSetExcluded(repo, repo.repository_instance_id, !excluded)}>{t(`repositories.actions.${excluded ? "include" : "exclude"}`)}</button>
          <button className="button button--secondary button--small" type="button" onClick={() => onOpenRuns(repo.repository.repository_id)}>{t("repositories.actions.runs")}</button>
          <button className="button button--secondary button--small" type="button" onClick={() => onOpenDependencies(repo.repository.repository_id)}>{t("repositories.actions.dependencies")}</button>
        </div></td>
      </tr>;
    })}</tbody></table></div>
    {pageCount > 1 ? <nav className="game-tape-repository-pagination" aria-label={t("repositories.paginationLabel")}>
      <button className="button button--secondary button--compact" type="button" disabled={safePage === 0} onClick={() => setPage(Math.max(0, safePage - 1))}>{t("common.previous")}</button>
      <span>{t("repositories.pageRange", { from, to, total: visible.length })}</span>
      <button className="button button--secondary button--compact" type="button" disabled={safePage + 1 >= pageCount} onClick={() => setPage(Math.min(pageCount - 1, safePage + 1))}>{t("common.next")}</button>
    </nav> : null}
  </>;
}
