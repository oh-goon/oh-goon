import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { LeagueMap } from "@generated/models";
import { setVisibleRepositorySelection, repositorySelectionView } from "@/features/league/repositorySelection";

const REPOSITORY_SELECTION_PAGE_SIZE = 100;

interface Props {
  repositories: NonNullable<LeagueMap["teams"]>;
  selectedIds: string[];
  onChange: (ids: string[]) => void;
}

export function RepositorySelectionPanel({ repositories, selectedIds, onChange }: Props) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [selectedOnly, setSelectedOnly] = useState(false);
  const [page, setPage] = useState(0);
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
  const duplicateNames = useMemo(() => {
    const counts = new Map<string, number>();
    for (const entry of repositories) counts.set(entry.repository.name, (counts.get(entry.repository.name) ?? 0) + 1);
    return new Set([...counts.entries()].filter(([, count]) => count > 1).map(([name]) => name));
  }, [repositories]);
  const view = useMemo(
    () => repositorySelectionView(repositories, selectedIds, query, selectedOnly),
    [query, repositories, selectedIds, selectedOnly],
  );

  const pageCount = Math.max(1, Math.ceil(view.visibleRepositories.length / REPOSITORY_SELECTION_PAGE_SIZE));
  const boundedPage = Math.min(page, pageCount - 1);
  useEffect(() => { if (page !== boundedPage) setPage(boundedPage); }, [boundedPage, page]);
  const pageStart = boundedPage * REPOSITORY_SELECTION_PAGE_SIZE;
  const pageRepositories = view.visibleRepositories.slice(pageStart, pageStart + REPOSITORY_SELECTION_PAGE_SIZE);
  useEffect(() => setPage(0), [query, selectedOnly]);

  const setOne = (id: string, checked: boolean) => {
    onChange(checked ? [...selectedIds, id] : selectedIds.filter((selectedId) => selectedId !== id));
  };

  return <section className="repository-selector shared-run-stage shared-run-stage--repositories">
    <div className="field-heading">
      <span>{t("sharedPlans.repositories")}</span>
      <span className="button-row">
        <button className="button button--secondary button--small" type="button" onClick={() => onChange(repositories.map((entry) => entry.repository.repository_id))}>{t("sharedPlans.allRepositories", { count: repositories.length })}</button>
        {selectedIds.length > 0 ? <button className="button button--quiet button--small" type="button" onClick={() => onChange([])}>{t("sharedPlans.clear")}</button> : null}
      </span>
    </div>
    <div className="repository-selector__toolbar">
      <label className="repository-selector__search">
        <span>{t("sharedPlans.repositorySelection.searchLabel")}</span>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("sharedPlans.repositorySelection.searchPlaceholder")}
        />
      </label>
      <label className="checkbox-inline">
        <input type="checkbox" checked={selectedOnly} onChange={(event) => setSelectedOnly(event.target.checked)} />
        <span>{t("sharedPlans.repositorySelection.selectedOnly")}</span>
      </label>
    </div>
    <div className="repository-selector__summary" role="status" aria-live="polite">
      <strong>{query.trim() || selectedOnly
        ? t("sharedPlans.repositorySelection.scope", { selected: view.selectedCount, visible: view.visibleCount, total: view.totalCount })
        : t("sharedPlans.repositorySelection.scopeSimple", { selected: view.selectedCount, total: view.totalCount })}</strong>
      {view.hiddenSelectedCount > 0 ? <span>{t("sharedPlans.repositorySelection.hiddenSelected", { count: view.hiddenSelectedCount })}</span> : null}
    </div>
    {query.trim() || selectedOnly ? <div className="button-row">
      <button
        className="button button--quiet button--small"
        type="button"
        disabled={view.visibleIds.length === 0}
        onClick={() => onChange(setVisibleRepositorySelection(selectedIds, view.visibleIds, true))}
      >{t("sharedPlans.repositorySelection.selectVisible")}</button>
      <button
        className="button button--quiet button--small"
        type="button"
        disabled={view.visibleIds.length === 0}
        onClick={() => onChange(setVisibleRepositorySelection(selectedIds, view.visibleIds, false))}
      >{t("sharedPlans.repositorySelection.clearVisible")}</button>
    </div> : null}
    <div className="repository-selector__list" role="group" aria-label={t("sharedPlans.repositorySelection.listLabel")}>
      {view.visibleRepositories.length === 0 ? <div className="empty-inline" role="status"><span>{t("sharedPlans.repositorySelection.noMatches")}</span><button className="text-button" type="button" onClick={() => { setQuery(""); setSelectedOnly(false); }}>{t("common.clearFilters")}</button></div> : null}
      {pageRepositories.map((entry) => <label key={entry.repository.repository_id} className="checkbox-row repository-selector__row">
        <input
          type="checkbox"
          checked={selected.has(entry.repository.repository_id)}
          onChange={(event) => setOne(entry.repository.repository_id, event.target.checked)}
        />
        <span><strong>{entry.repository.name}</strong>{duplicateNames.has(entry.repository.name) ? <small className="repository-selection-path">{entry.repository.path}</small> : null}</span>
      </label>)}
    </div>
    {view.visibleRepositories.length > REPOSITORY_SELECTION_PAGE_SIZE ? <nav className="repository-selection-pagination" aria-label={t("sharedPlans.repositorySelection.paginationLabel")}><button className="button button--secondary button--small" type="button" disabled={boundedPage === 0} onClick={() => setPage(Math.max(0, boundedPage - 1))}>{t("common.previous")}</button><span>{t("sharedPlans.repositorySelection.pageRange", { from: pageStart + 1, to: Math.min(pageStart + pageRepositories.length, view.visibleRepositories.length), total: view.visibleRepositories.length })}</span><button className="button button--secondary button--small" type="button" disabled={boundedPage + 1 >= pageCount} onClick={() => setPage(Math.min(pageCount - 1, boundedPage + 1))}>{t("common.next")}</button></nav> : null}
  </section>;
}
