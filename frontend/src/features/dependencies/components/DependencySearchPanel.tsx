import { useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import type { ReadResource } from "@/domain/reads/ReadResource";
import type { DependencySearchResult } from "@generated/models";

interface Props {
  query: string;
  resource: ReadResource<DependencySearchResult>;
  onSearch: (query: string) => void;
  onPage: (offset: number) => void;
  onOpenPackage: (packageName: string) => void;
}

export function DependencySearchPanel({ query, resource, onSearch, onPage, onOpenPackage }: Props) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(query);
  useEffect(() => setDraft(query), [query]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalized = draft.trim();
    if (normalized) onSearch(normalized);
  };

  const loading = resource.status === "read-loading" || resource.status === "read-refreshing";
  const data = resource.data;
  const items = data?.items ?? [];

  return <section className="panel dependency-search-panel" aria-busy={loading}>
    <div className="section-heading"><div>
      <span className="eyebrow">{t("dependencies.discoveryEyebrow")}</span>
      <h2>{t("dependencies.discoveryTitle")}</h2>
      <p>{t("dependencies.discoveryDescription")}</p>
    </div></div>
    <form className="dependency-search-form" onSubmit={submit} role="search">
      <label><span>{t("dependencies.searchLabel")}</span><input type="search" value={draft} onChange={(event: ChangeEvent<HTMLInputElement>) => setDraft(event.target.value)} placeholder={t("dependencies.searchPlaceholder")} /></label>
      <button className="button button--primary" type="submit" disabled={!draft.trim()}>{t("dependencies.searchAction")}</button>
    </form>
    {loading && data === null ? <p className="muted loading-state" role="status">{t("dependencies.loading")}</p> : null}
    {resource.status === "read-error" ? <p className="notice notice--warning" role="alert">{resource.failure?.message}</p> : null}
    {data && items.length === 0 ? <p className="dependency-empty" role="status">{t("dependencies.noSearchResults", { query: data.query })}</p> : null}
    {items.length > 0 ? <div className="dependency-search-results" role="group" aria-label={t("dependencies.searchResults")}>
      {items.map((item) => <button className="dependency-search-result" type="button" key={`${item.ecosystem}:${item.package_name}`} onClick={() => onOpenPackage(item.package_name)}>
        <span><strong>{item.package_name}</strong><small>{item.ecosystem}</small></span>
        <span className="dependency-result-facts"><b>{t("dependencies.repositoryCount", { count: item.repositories })}</b><small>{t("dependencies.resolvedVersionCount", { count: item.resolved_versions })}</small></span>
      </button>)}
    </div> : null}
    {data && data.total > data.limit ? <DependencyPager offset={data.offset} limit={data.limit} total={data.total} shown={items.length} onPage={onPage} /> : null}
  </section>;
}

function DependencyPager({ offset, limit, total, shown, onPage }: { offset: number; limit: number; total: number; shown: number; onPage: (offset: number) => void }) {
  const { t } = useTranslation();
  return <nav className="dependency-pagination" aria-label={t("dependencies.searchPaginationLabel")}>
    <button className="button button--secondary" type="button" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - limit))}>{t("dependencies.previous")}</button>
    <span>{t("dependencies.pageRange", { from: offset + 1, to: Math.min(offset + shown, total), total })}</span>
    <button className="button button--secondary" type="button" disabled={offset + limit >= total} onClick={() => onPage(offset + limit)}>{t("dependencies.next")}</button>
  </nav>;
}
