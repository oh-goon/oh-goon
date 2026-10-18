import { type ChangeEvent, type FormEvent, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { lastValidPageOffset } from "@/features/scout/scoutAuthority";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import type {
  ScoutComparisonEnvironment,
  ScoutCoverage,
  ScoutFinding,
  ScoutFindingCount,
  ScoutFindingsPage,
  ScoutGoonDetail,
  ScoutGoonLeadersPage,
  ScoutHealth,
  ScoutMetricDictionary,
  ScoutOverview,
  ScoutPlanDetail,
  ScoutPlanSummary,
  ScoutRankingRow,
  ScoutRepositoryDetail,
  ScoutRunSample,
  ScoutSearchPage,
  ScoutSearchResult,
  ScoutStepSummary,
  ScoutTrendPoint,
} from "@generated/models";

export type ScoutHours = number;
export type ScoutFindingCategory = ScoutFinding["category"];
type ScoutPageSection = NonNullable<ScoutPlanDetail["page_section"]>;

export type ScoutRoute =
  | { readonly view: "overview"; readonly hours: ScoutHours }
  | { readonly view: "search"; readonly hours: ScoutHours; readonly query: string; readonly offset?: number }
  | { readonly view: "findings"; readonly hours: ScoutHours; readonly category: ScoutFindingCategory; readonly offset?: number }
  | { readonly view: "repository"; readonly hours: ScoutHours; readonly repositoryId: string; readonly planId?: string; readonly section?: ScoutPageSection; readonly offset?: number }
  | { readonly view: "plan-detail"; readonly hours: ScoutHours; readonly planId: string; readonly section?: ScoutPageSection; readonly offset?: number }
  | { readonly view: "goon"; readonly hours: ScoutHours; readonly goonName: string; readonly sourceSha256?: string; readonly section?: ScoutPageSection; readonly offset?: number }
  | { readonly view: "goon-leaders"; readonly hours: ScoutHours; readonly offset?: number };

const SCOUT_PRESET_HOURS = new Set<ScoutHours>([1, 24, 168, 720]);
const SCOUT_MAX_HOURS = 24 * 90;
const SCOUT_DETAIL_SECTIONS = new Set<ScoutPageSection>([
  "plans",
  "goons",
  "steps",
  "repositories",
  "versions",
]);

const SCOUT_CATEGORIES = new Set<ScoutFindingCategory>([
  "HEAVY_HITTER",
  "MOST_ACTIVE",
  "EXPENSIVE_RUN",
  "CPU_OUTLIER",
  "MEMORY_OUTLIER",
  "RUN_SPIKE",
  "GOON_REGRESSION",
  "FAILURE_WASTE",
]);

export function scoutRouteFromHash(hash: string): ScoutRoute {
  const query = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "";
  const params = new URLSearchParams(query);
  const requestedHours = Number(params.get("hours") ?? "24");
  const hours: ScoutHours = Number.isInteger(requestedHours) && requestedHours >= 1 && requestedHours <= SCOUT_MAX_HOURS
    ? requestedHours
    : 24;
  const view = params.get("view");
  const requestedSection = params.get("section");
  const section = requestedSection !== null && SCOUT_DETAIL_SECTIONS.has(requestedSection as ScoutPageSection)
    ? requestedSection as ScoutPageSection
    : undefined;
  const requestedDetailOffset = Number(params.get("offset") ?? "0");
  const detailOffset = Number.isInteger(requestedDetailOffset) && requestedDetailOffset > 0 ? requestedDetailOffset : undefined;
  if (view === "search") {
    const query = params.get("q")?.trim();
    if (query) {
      const requestedOffset = Number(params.get("offset") ?? "0");
      const offset = Number.isInteger(requestedOffset) && requestedOffset > 0 ? requestedOffset : undefined;
      return { view, hours, query, ...(offset ? { offset } : {}) };
    }
  }
  if (view === "findings") {
    const category = params.get("category") as ScoutFindingCategory | null;
    if (category !== null && SCOUT_CATEGORIES.has(category)) {
      const requestedOffset = Number(params.get("offset") ?? "0");
      const offset = Number.isInteger(requestedOffset) && requestedOffset > 0 ? requestedOffset : undefined;
      return { view, hours, category, ...(offset ? { offset } : {}) };
    }
  }
  if (view === "goon-leaders") {
    const requestedOffset = Number(params.get("offset") ?? "0");
    const offset = Number.isInteger(requestedOffset) && requestedOffset > 0 ? requestedOffset : undefined;
    return { view, hours, ...(offset ? { offset } : {}) };
  }
  if (view === "repository") {
    const repositoryId = params.get("repository_id");
    if (repositoryId) {
      const planId = params.get("plan_id") ?? undefined;
      const repositorySection = section === "plans" || section === "goons" || section === "steps" ? section : undefined;
      return { view, hours, repositoryId, ...(planId ? { planId } : {}), ...(repositorySection ? { section: repositorySection } : {}), ...(repositorySection && detailOffset ? { offset: detailOffset } : {}) };
    }
  }
  if (view === "plan-detail") {
    const planId = params.get("plan_id");
    if (planId) {
      const planSection = section === "repositories" || section === "goons" || section === "steps" ? section : undefined;
      return { view, hours, planId, ...(planSection ? { section: planSection } : {}), ...(planSection && detailOffset ? { offset: detailOffset } : {}) };
    }
  }
  if (view === "goon") {
    const goonName = params.get("goon");
    if (goonName) {
      const sourceSha256 = params.get("source") ?? undefined;
      const goonSection = section === "versions" || section === "repositories" || section === "steps" ? section : undefined;
      return { view, hours, goonName, ...(sourceSha256 ? { sourceSha256 } : {}), ...(goonSection ? { section: goonSection } : {}), ...(goonSection && detailOffset ? { offset: detailOffset } : {}) };
    }
  }
  return { view: "overview", hours };
}

export function scoutRouteWithHours(route: ScoutRoute, hours: ScoutHours): ScoutRoute {
  const next = { ...route, hours } as ScoutRoute & { offset?: number };
  delete next.offset;
  return next as ScoutRoute;
}

export function scoutRouteQuery(route: ScoutRoute): Record<string, string> {
  const query: Record<string, string> = { hours: String(route.hours) };
  if (route.view === "search") return { ...query, view: route.view, q: route.query, ...(route.offset ? { offset: String(route.offset) } : {}) };
  if (route.view === "findings") return { ...query, view: route.view, category: route.category, ...(route.offset ? { offset: String(route.offset) } : {}) };
  if (route.view === "goon-leaders") return { ...query, view: route.view, ...(route.offset ? { offset: String(route.offset) } : {}) };
  if (route.view === "repository") return { ...query, view: route.view, repository_id: route.repositoryId, ...(route.planId ? { plan_id: route.planId } : {}), ...(route.section ? { section: route.section } : {}), ...(route.offset ? { offset: String(route.offset) } : {}) };
  if (route.view === "plan-detail") return { ...query, view: route.view, plan_id: route.planId, ...(route.section ? { section: route.section } : {}), ...(route.offset ? { offset: String(route.offset) } : {}) };
  if (route.view === "goon") {
    return {
      ...query,
      view: route.view,
      goon: route.goonName,
      ...(route.sourceSha256 ? { source: route.sourceSha256 } : {}),
      ...(route.section ? { section: route.section } : {}),
      ...(route.offset ? { offset: String(route.offset) } : {}),
    };
  }
  return query;
}

interface Props {
  readonly route: ScoutRoute;
  readonly onNavigate: (route: ScoutRoute) => void;
  readonly onOpenRun: (runId: string) => void;
}

type TrendMetric = "cpu" | "memory" | "runs";

const SCOUT_WINDOW_POLL_MS = 60_000;
const SCOUT_HEALTH_POLL_MS = 5_000;

export function ScoutFeature({ route, onNavigate, onOpenRun }: Props) {
  const { t } = useTranslation();
  const client = useApiClient();
  const [trendMetric, setTrendMetric] = useState<TrendMetric>("cpu");
  const overviewEnabled = route.view === "overview";
  const searchEnabled = route.view === "search";
  const findingsEnabled = route.view === "findings";
  const repositoryEnabled = route.view === "repository";
  const planEnabled = route.view === "plan-detail";
  const goonEnabled = route.view === "goon";
  const goonLeadersEnabled = route.view === "goon-leaders";

  const overviewRequest = useCallback((signal: AbortSignal) => client.scoutOverview(route.hours, 20, { signal }), [client, route.hours]);
  const searchRequest = useCallback((signal: AbortSignal) => client.scoutSearch(route.view === "search" ? route.query : "disabled", route.hours, route.view === "search" ? route.offset ?? 0 : 0, 20, { signal }), [client, route]);
  const findingsRequest = useCallback((signal: AbortSignal) => client.scoutFindings(route.hours, route.view === "findings" ? route.category : undefined, route.view === "findings" ? route.offset ?? 0 : 0, 20, { signal }), [client, route]);
  const repositoryRequest = useCallback((signal: AbortSignal) => client.scoutRepository(route.view === "repository" ? route.repositoryId : "disabled", route.hours, route.view === "repository" ? route.planId : undefined, route.view === "repository" ? route.section : undefined, route.view === "repository" ? route.offset ?? 0 : 0, 100, { signal }), [client, route]);
  const planRequest = useCallback((signal: AbortSignal) => client.scoutPlan(route.view === "plan-detail" ? route.planId : "disabled", route.hours, route.view === "plan-detail" ? route.section : undefined, route.view === "plan-detail" ? route.offset ?? 0 : 0, 100, { signal }), [client, route]);
  const goonRequest = useCallback((signal: AbortSignal) => client.scoutGoon(route.view === "goon" ? route.goonName : "disabled", route.view === "goon" ? route.sourceSha256 : undefined, route.hours, route.view === "goon" ? route.section : undefined, route.view === "goon" ? route.offset ?? 0 : 0, 100, { signal }), [client, route]);
  const goonLeadersRequest = useCallback((signal: AbortSignal) => client.scoutGoonLeaders(route.hours, route.view === "goon-leaders" ? route.offset ?? 0 : 0, 20, { signal }), [client, route]);
  const metricsRequest = useCallback((signal: AbortSignal) => client.scoutMetrics({ signal }), [client]);
  const healthRequest = useCallback((signal: AbortSignal) => client.scoutHealth({ signal }), [client]);

  const overview = useReadResource<ScoutOverview>({ scope: "scout-overview", source: "scout-overview", identity: String(route.hours), enabled: overviewEnabled, errorMessage: t("scout.errorTitle"), request: overviewRequest, pollIntervalMs: SCOUT_WINDOW_POLL_MS, validate: (value) => { if (value.window.hours !== route.hours) throw new Error("Scout overview response window does not match the requested window"); } });
  const search = useReadResource<ScoutSearchPage>({ scope: "scout-search", source: "scout-search", identity: searchEnabled ? canonicalIdentity({ hours: route.hours, query: route.query, offset: route.offset ?? 0 }) : null, enabled: searchEnabled, errorMessage: t("scout.errorTitle"), request: searchRequest, pollIntervalMs: SCOUT_WINDOW_POLL_MS, validate: (value) => { if (route.view !== "search" || value.window.hours !== route.hours || value.query !== route.query || value.offset !== (route.offset ?? 0) || value.limit !== 20) throw new Error("Scout search response coordinates do not match the requested search"); } });
  const findings = useReadResource<ScoutFindingsPage>({ scope: "scout-findings", source: "scout-findings", identity: findingsEnabled ? canonicalIdentity({ hours: route.hours, category: route.category, offset: route.offset ?? 0 }) : null, enabled: findingsEnabled, errorMessage: t("scout.errorTitle"), request: findingsRequest, pollIntervalMs: SCOUT_WINDOW_POLL_MS, validate: (value) => { if (route.view !== "findings" || value.window.hours !== route.hours || value.category !== route.category || value.offset !== (route.offset ?? 0) || value.limit !== 20) throw new Error("Scout findings response coordinates do not match the requested findings"); } });
  const repository = useReadResource<ScoutRepositoryDetail>({ scope: "scout-repository", source: "scout-repository", identity: repositoryEnabled ? canonicalIdentity({ hours: route.hours, repositoryId: route.repositoryId, planId: route.planId ?? null, section: route.section ?? null, offset: route.offset ?? 0 }) : null, enabled: repositoryEnabled, errorMessage: t("scout.errorTitle"), request: repositoryRequest, pollIntervalMs: SCOUT_WINDOW_POLL_MS, validate: (value) => { if (route.view !== "repository" || value.window.hours !== route.hours || value.repository.repository_id !== route.repositoryId || value.selected_plan_id !== (route.planId ?? null) || value.page_section !== (route.section ?? null) || value.page_offset !== (route.offset ?? 0) || value.page_limit !== 100) throw new Error("Scout repository response coordinates do not match the requested repository"); } });
  const plan = useReadResource<ScoutPlanDetail>({ scope: "scout-plan", source: "scout-plan", identity: planEnabled ? canonicalIdentity({ hours: route.hours, planId: route.planId, section: route.section ?? null, offset: route.offset ?? 0 }) : null, enabled: planEnabled, errorMessage: t("scout.errorTitle"), request: planRequest, pollIntervalMs: SCOUT_WINDOW_POLL_MS, validate: (value) => { if (route.view !== "plan-detail" || value.window.hours !== route.hours || value.plan_id !== route.planId || value.page_section !== (route.section ?? null) || value.page_offset !== (route.offset ?? 0) || value.page_limit !== 100) throw new Error("Scout Plan response coordinates do not match the requested Plan"); } });
  const goon = useReadResource<ScoutGoonDetail>({ scope: "scout-goon", source: "scout-goon", identity: goonEnabled ? canonicalIdentity({ hours: route.hours, goonName: route.goonName, sourceSha256: route.sourceSha256 ?? null, section: route.section ?? null, offset: route.offset ?? 0 }) : null, enabled: goonEnabled, errorMessage: t("scout.errorTitle"), request: goonRequest, pollIntervalMs: SCOUT_WINDOW_POLL_MS, validate: (value) => { if (route.view !== "goon" || value.window.hours !== route.hours || value.goon_name !== route.goonName || value.selected_source_sha256 !== (route.sourceSha256 ?? null) || value.page_section !== (route.section ?? null) || value.page_offset !== (route.offset ?? 0) || value.page_limit !== 100) throw new Error("Scout Goon response coordinates do not match the requested Goon"); } });
  const goonLeaders = useReadResource<ScoutGoonLeadersPage>({ scope: "scout-goon-leaders", source: "scout-goon-leaders", identity: goonLeadersEnabled ? canonicalIdentity({ hours: route.hours, offset: route.offset ?? 0 }) : null, enabled: goonLeadersEnabled, errorMessage: t("scout.errorTitle"), request: goonLeadersRequest, pollIntervalMs: SCOUT_WINDOW_POLL_MS, validate: (value) => { if (route.view !== "goon-leaders" || value.window.hours !== route.hours || value.offset !== (route.offset ?? 0) || value.limit !== 20) throw new Error("Scout Goon leaders response coordinates do not match the requested page"); } });
  const metrics = useReadResource<ScoutMetricDictionary>({ scope: "scout-metrics", source: "scout-metrics", identity: "metric-dictionary", errorMessage: t("scout.errorTitle"), request: metricsRequest });
  const health = useReadResource<ScoutHealth>({ scope: "scout-health", source: "scout-health", identity: "health", enabled: overviewEnabled, errorMessage: t("scout.errorTitle"), request: healthRequest, pollIntervalMs: SCOUT_HEALTH_POLL_MS });

  const active = route.view === "overview" ? overview.resource : route.view === "search" ? search.resource : route.view === "findings" ? findings.resource : route.view === "goon-leaders" ? goonLeaders.resource : route.view === "repository" ? repository.resource : route.view === "plan-detail" ? plan.resource : goon.resource;
  useEffect(() => {
    const currentOffset = route.view === "overview" ? 0 : route.offset ?? 0;
    if (currentOffset === 0 || active.data === null) return;
    const page = scoutPagePopulation(route, active.data);
    if (page === null || page.offset < page.total) return;
    const nextOffset = lastValidPageOffset(page.total, page.limit);
    onNavigate(scoutRouteWithOffset(route, nextOffset));
  }, [active.data, onNavigate, route]);
  const error = active.failure?.message;
  const loading = active.data === null && (active.status === "read-loading" || active.status === "read-idle");
  const changeHours = (hours: ScoutHours) => onNavigate(scoutRouteWithHours(route, hours));
  return <main className="page scout-page">
    <PageHeading
      eyebrow={t("scout.eyebrow")}
      title={t("scout.title")}
      description={t("scout.description")}
      action={<ScoutWindowControl hours={route.hours} onChange={changeHours} />}
    />
    <ScoutSearchForm currentQuery={route.view === "search" ? route.query : ""} onSearch={(query) => onNavigate({ view: "search", hours: route.hours, query })} />
    {route.view !== "overview" ? <ScoutBreadcrumbs route={route} onNavigate={onNavigate} /> : null}
    {error ? <ErrorBanner title={t("scout.errorTitle")} message={`${error} ${t("scout.errorSuffix")}`} /> : null}
    {loading ? <div className="league-loading" role="status"><span className="loading-dot" aria-hidden="true" />{t("scout.loading")}</div> : null}
    {route.view === "overview" && overview.resource.data ? <ScoutOverviewView data={overview.resource.data} metrics={metrics.resource.data} health={health.resource.data} trendMetric={trendMetric} onTrendMetric={setTrendMetric} onNavigate={onNavigate} /> : null}
    {route.view === "search" && search.resource.data ? <ScoutSearchView page={search.resource.data} hours={route.hours} onNavigate={onNavigate} /> : null}
    {route.view === "findings" && findings.resource.data ? <ScoutFindingsView page={findings.resource.data} category={route.category} hours={route.hours} onNavigate={onNavigate} onOpenRun={onOpenRun} /> : null}
    {route.view === "goon-leaders" && goonLeaders.resource.data ? <ScoutGoonLeadersView page={goonLeaders.resource.data} hours={route.hours} onNavigate={onNavigate} /> : null}
    {route.view === "repository" && repository.resource.data ? <ScoutRepositoryView data={repository.resource.data} hours={route.hours} onNavigate={onNavigate} onOpenRun={onOpenRun} /> : null}
    {route.view === "plan-detail" && plan.resource.data ? <ScoutPlanView data={plan.resource.data} hours={route.hours} onNavigate={onNavigate} onOpenRun={onOpenRun} /> : null}
    {route.view === "goon" && goon.resource.data ? <ScoutGoonView data={goon.resource.data} hours={route.hours} trendMetric={trendMetric} onTrendMetric={setTrendMetric} onNavigate={onNavigate} onOpenRun={onOpenRun} /> : null}
  </main>;
}

function ScoutBreadcrumbs({ route, onNavigate }: { route: Exclude<ScoutRoute, { view: "overview" }>; onNavigate: (route: ScoutRoute) => void }) {
  const { t } = useTranslation();
  const overview = { view: "overview", hours: route.hours } as const;
  const segments: Array<{ label: string; route?: ScoutRoute }> = [];
  if (route.view === "search") segments.push({ label: t("scout.searchResults") });
  if (route.view === "findings") segments.push({ label: categoryLabel(t, route.category) });
  if (route.view === "goon-leaders") segments.push({ label: t("scout.goonLeaders") });
  if (route.view === "repository") {
    segments.push({ label: route.repositoryId, ...(route.planId ? { route: { view: "repository", hours: route.hours, repositoryId: route.repositoryId } as ScoutRoute } : {}) });
    if (route.planId) segments.push({ label: route.planId });
  }
  if (route.view === "plan-detail") segments.push({ label: `${t("scout.plan")}: ${route.planId}` });
  if (route.view === "goon") {
    segments.push({ label: route.goonName, ...(route.sourceSha256 ? { route: { view: "goon", hours: route.hours, goonName: route.goonName } as ScoutRoute } : {}) });
    if (route.sourceSha256) segments.push({ label: shortSha(route.sourceSha256) });
  }
  return <nav className="button-row scout-breadcrumbs" aria-label={t("scout.breadcrumbs")}><button type="button" className="text-button" onClick={() => onNavigate(overview)}>{t("scout.title")}</button>{segments.map((segment, index) => <span key={`${segment.label}-${index}`}> / {segment.route ? <button type="button" className="text-button" onClick={() => onNavigate(segment.route!)}>{segment.label}</button> : <span aria-current="page">{segment.label}</span>}</span>)}</nav>;
}


function ScoutWindowControl({ hours, onChange }: { hours: ScoutHours; onChange: (hours: ScoutHours) => void }) {
  const { t } = useTranslation();
  const [customOpen, setCustomOpen] = useState(false);
  const preset = SCOUT_PRESET_HOURS.has(hours);
  const submitCustom = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = Number(new FormData(event.currentTarget).get("scout-hours"));
    if (Number.isInteger(value) && value >= 1 && value <= SCOUT_MAX_HOURS) {
      setCustomOpen(false);
      onChange(value);
    }
  };
  return <div className="button-row"><label className="scout-window"><span>{t("scout.window")}</span><select value={preset ? String(hours) : "custom-current"} onChange={(event: ChangeEvent<HTMLSelectElement>) => { const value = Number(event.target.value); if (Number.isInteger(value)) { setCustomOpen(false); onChange(value); } }}>{[1, 24, 168, 720].map((value) => <option key={value} value={value}>{t(`scout.hours.${value}`)}</option>)}{!preset ? <option value="custom-current">{t("scout.customCurrent", { hours })}</option> : null}</select></label><button type="button" className="text-button" onClick={() => setCustomOpen((open: boolean) => !open)}>{t("scout.customWindow")}</button>{customOpen ? <form onSubmit={submitCustom} className="button-row"><label><span>{t("scout.customHours")}</span><input name="scout-hours" type="number" min="1" max={SCOUT_MAX_HOURS} step="1" defaultValue={hours} /></label><button type="submit" className="button button--secondary">{t("scout.applyWindow")}</button></form> : null}</div>;
}

function scoutRouteWithOffset(route: ScoutRoute, offset: number): ScoutRoute {
  if (route.view === "overview") return route;
  const next = { ...route } as ScoutRoute & { offset?: number };
  if (offset > 0) next.offset = offset; else delete next.offset;
  return next;
}

function scoutPagePopulation(route: ScoutRoute, value: ScoutOverview | ScoutSearchPage | ScoutFindingsPage | ScoutGoonLeadersPage | ScoutRepositoryDetail | ScoutPlanDetail | ScoutGoonDetail): { offset: number; limit: number; total: number } | null {
  if (route.view === "search" && "query" in value) return { offset: value.offset, limit: value.limit, total: value.total };
  if (route.view === "findings" && "category" in value && "items" in value) return { offset: value.offset, limit: value.limit, total: value.total };
  if (route.view === "goon-leaders" && "items" in value && "offset" in value && !('query' in value) && !('category' in value)) return { offset: value.offset, limit: value.limit, total: value.total };
  if (route.view === "repository" && "repository" in value) {
    const total = route.section === "plans" ? value.plan_total : route.section === "goons" ? value.goon_total : route.section === "steps" ? value.step_total : null;
    return total === null ? null : { offset: value.page_offset, limit: value.page_limit, total };
  }
  if (route.view === "plan-detail" && "plan_id" in value && "repository_total" in value) {
    const total = route.section === "repositories" ? value.repository_total : route.section === "goons" ? value.goon_total : route.section === "steps" ? value.step_total : null;
    return total === null ? null : { offset: value.page_offset, limit: value.page_limit, total };
  }
  if (route.view === "goon" && "goon_name" in value) {
    const total = route.section === "versions" ? value.version_total : route.section === "repositories" ? value.repository_total : route.section === "steps" ? value.step_total : null;
    return total === null ? null : { offset: value.page_offset, limit: value.page_limit, total };
  }
  return null;
}

function ScoutSearchForm({ currentQuery, onSearch }: { currentQuery: string; onSearch: (query: string) => void }) {
  const { t } = useTranslation();
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const query = String(form.get("scout-query") ?? "").trim();
    if (query) onSearch(query);
  };
  return <form className="button-row scout-search-form" role="search" onSubmit={submit}><label className="field field--grow"><span>{t("scout.searchLabel")}</span><input key={currentQuery} name="scout-query" type="search" defaultValue={currentQuery} placeholder={t("scout.searchPlaceholder")} maxLength={120} required /></label><button type="submit" className="button button--secondary">{t("scout.searchAction")}</button></form>;
}

function ScoutSearchView({ page, hours, onNavigate }: { page: ScoutSearchPage; hours: ScoutHours; onNavigate: (route: ScoutRoute) => void }) {
  const { t } = useTranslation();
  const items = page.items ?? [];
  const first = page.total === 0 ? 0 : page.offset + 1;
  const last = Math.min(page.offset + items.length, page.total);
  const previousOffset = Math.max(0, page.offset - page.limit);
  const nextOffset = page.offset + page.limit;
  return <section className="panel"><header className="section-heading"><div><span className="eyebrow">{t("scout.searchResults")}</span><h2>{t("scout.searchTitle", { query: page.query })}</h2></div><small>{t("scout.searchOrder")} · {t("scout.resultRange", { first, last, total: page.total })}</small></header>{items.length === 0 ? <p className="empty-inline">{t("scout.searchNone")}</p> : <div className="compact-card-list">{items.map((item) => <button type="button" className="compact-card" key={`${item.entity_kind}-${item.key}`} onClick={() => onNavigate(searchResultRoute(item, hours))}><span><strong>{item.display_name}</strong><small>{t(`scout.searchKinds.${item.entity_kind}`)} · {item.key}</small></span><em aria-hidden="true">→</em></button>)}</div>}<footer className="button-row"><button type="button" className="text-button" disabled={page.offset === 0} onClick={() => onNavigate({ view: "search", hours, query: page.query, ...(previousOffset ? { offset: previousOffset } : {}) })}>← {t("scout.previous")}</button><button type="button" className="text-button" disabled={nextOffset >= page.total} onClick={() => onNavigate({ view: "search", hours, query: page.query, offset: nextOffset })}>{t("scout.next")} →</button></footer></section>;
}

function searchResultRoute(item: ScoutSearchResult, hours: ScoutHours): ScoutRoute {
  if (item.entity_kind === "REPOSITORY" && item.repository_id) return { view: "repository", hours, repositoryId: item.repository_id };
  if (item.entity_kind === "PLAN" && item.plan_id) return { view: "plan-detail", hours, planId: item.plan_id };
  if (item.entity_kind === "GOON" && item.goon_name) return { view: "goon", hours, goonName: item.goon_name };
  return { view: "overview", hours };
}

function ScoutPlanView({ data, hours, onNavigate, onOpenRun }: { data: ScoutPlanDetail; hours: ScoutHours; onNavigate: (route: ScoutRoute) => void; onOpenRun: (runId: string) => void }) {
  const { t } = useTranslation();
  const page = (section: ScoutPageSection) => ({ offset: data.page_section === section ? data.page_offset : 0, limit: data.page_limit });
  return <><section className="panel"><span className="eyebrow">{t("scout.plan")}</span><h2>{data.plan_name}</h2><p className="muted-text">{data.plan_id}</p></section><RankingPanel title={t("scout.repositories")} description={t("scout.sortedByCpu")} rows={data.repositories ?? []} total={data.repository_total} {...page("repositories")} onPage={(offset) => onNavigate({ view: "plan-detail", hours, planId: data.plan_id, section: "repositories", ...(offset ? { offset } : {}) })} onOpen={(row) => row.repository_id && onNavigate({ view: "repository", hours, repositoryId: row.repository_id, planId: data.plan_id })} /><RankingPanel title={t("scout.goonLeaders")} description={t("scout.whereResourcesWent")} rows={data.goons ?? []} total={data.goon_total} {...page("goons")} onPage={(offset) => onNavigate({ view: "plan-detail", hours, planId: data.plan_id, section: "goons", ...(offset ? { offset } : {}) })} onOpen={(row) => row.goon_name && onNavigate({ view: "goon", hours, goonName: row.goon_name, ...(row.goon_source_sha256 ? { sourceSha256: row.goon_source_sha256 } : {}) })} /><StepPanel steps={data.steps ?? []} total={data.step_total} {...page("steps")} onPage={(offset) => onNavigate({ view: "plan-detail", hours, planId: data.plan_id, section: "steps", ...(offset ? { offset } : {}) })} onOpenRun={onOpenRun} /><RunSamples samples={data.representative_runs ?? []} onOpenRun={onOpenRun} /></>;
}


function ScoutOverviewView({ data, metrics, health, trendMetric, onTrendMetric, onNavigate }: { data: ScoutOverview; metrics: ScoutMetricDictionary | null; health: ScoutHealth | null; trendMetric: TrendMetric; onTrendMetric: (metric: TrendMetric) => void; onNavigate: (route: ScoutRoute) => void }) {
  const { t } = useTranslation();
  if (!data.enabled) return <section className="notice notice--scope"><div><h2>{t("scout.disabledTitle")}</h2><p>{t("scout.disabled")}</p></div></section>;
  const kpis = [
    [t("scout.executions"), formatCount(data.executions), t("scout.startAssigned")],
    [t("scout.activeRepositories"), formatCount(data.active_repositories), t("scout.leagueActivity")],
    [t("scout.totalCpu"), formatCpu(data.totals.total_cpu_seconds), coverageText(t, data.totals.cpu_coverage)],
    [t("scout.peakMemory"), formatBytes(data.totals.peak_memory_bytes), coverageText(t, data.totals.memory_coverage)],
    [t("scout.failureRate"), `${data.failure_rate_percent.toFixed(1)}%`, t("scout.observedEvidence")],
  ];
  return <>
    <section className="summary-grid" aria-label={t("scout.leagueActivity")}>{kpis.map(([label, value, hint]) => <article className="summary-card" key={label}><span>{label}</span><strong>{value}</strong><small>{hint}</small></article>)}</section>
    <section className="form-grid">
      <article className="panel"><header><div><span className="eyebrow">{t("scout.needsScouting")}</span><h2>{t("scout.needsScouting")}</h2><p>{t("scout.needsDescription")}</p></div></header><div className="compact-card-list">{(data.needs_scouting ?? []).map((finding) => <FindingCountCard key={finding.category} finding={finding} onOpen={() => onNavigate({ view: "findings", hours: data.window.hours as ScoutHours, category: finding.category })} />)}</div></article>
      <article className="panel"><header className="section-heading"><div><span className="eyebrow">{t("scout.trend")}</span><h2>{t("scout.trend")}</h2></div><TrendMetricSelector metric={trendMetric} onChange={onTrendMetric} /></header><ScoutTrend points={data.trend ?? []} metric={trendMetric} /></article>
    </section>
    <RankingPanel title={t("scout.heavyHitters")} description={t("scout.highestCpu")} rows={data.heavy_hitters ?? []} total={data.heavy_hitter_total} onOpen={(row) => row.repository_id && onNavigate({ view: "repository", hours: data.window.hours as ScoutHours, repositoryId: row.repository_id })} />
    <div className="button-row" role="group" aria-label={t("scout.browseMore")}><button type="button" className="text-button" onClick={() => onNavigate({ view: "findings", hours: data.window.hours as ScoutHours, category: "HEAVY_HITTER" })}>{t("scout.heavyHitters")} →</button><button type="button" className="text-button" onClick={() => onNavigate({ view: "goon-leaders", hours: data.window.hours as ScoutHours })}>{t("scout.goonLeaders")} →</button><button type="button" className="text-button" onClick={() => onNavigate({ view: "findings", hours: data.window.hours as ScoutHours, category: "MOST_ACTIVE" })}>{categoryLabel(t, "MOST_ACTIVE")} →</button><button type="button" className="text-button" onClick={() => onNavigate({ view: "findings", hours: data.window.hours as ScoutHours, category: "EXPENSIVE_RUN" })}>{categoryLabel(t, "EXPENSIVE_RUN")} →</button></div>
    <RankingPanel title={t("scout.goonLeaders")} description={t("scout.sharedGoons")} rows={data.goon_leaders ?? []} total={data.goon_leader_total} onOpen={(row) => row.goon_name && onNavigate({ view: "goon", hours: data.window.hours as ScoutHours, goonName: row.goon_name, ...(row.goon_source_sha256 ? { sourceSha256: row.goon_source_sha256 } : {}) })} />
    <details className="panel scout-metric-details"><summary>{t("scout.showMetrics")}</summary>{health ? <><p>{t("scout.healthSummary", { revision: health.processed_journal_revision, runs: formatCount(health.retained_run_observations), steps: formatCount(health.retained_step_observations), days: health.detail_retention_days, runDays: health.run_retention_days })}</p><p>{t("scout.cacheSummary", { entries: health.snapshot_cache_entries, hits: health.snapshot_cache_hits, misses: health.snapshot_cache_misses })}</p><p>{t("scout.storageSummary", { bytes: formatStorageBytes(health.analytical_storage_bytes) })}</p>{health.last_snapshot_load_ms != null ? <p>{t("scout.snapshotLoadLatency", { milliseconds: health.last_snapshot_load_ms.toFixed(1) })}</p> : null}</> : null}{metrics ? <><p>{t("scout.measurementVersion", { version: metrics.measurement_schema_version })}</p><div className="form-grid">{metrics.definitions.map((metric) => <article className="compact-card" key={metric.metric}><strong>{metric.display_name}</strong><span>{metric.unit}</span><p>{metric.definition}</p><small>{t("scout.metricSource")}: {metric.source}</small><small>{t("scout.metricAggregation")}: {metric.aggregation}</small><small>{t("scout.metricMissingData")}: {metric.missing_data}</small><small>{t("scout.metricComparisonRule")}: {metric.comparison_rule}</small></article>)}</div></> : null}</details>
    <p className="muted-text">{t("scout.journalRevision", { revision: data.journal_revision })} · {t("scout.drillHint")}</p>
  </>;
}

function FindingCountCard({ finding, onOpen }: { finding: ScoutFindingCount; onOpen: () => void }) {
  const { t } = useTranslation();
  return <button type="button" className="compact-card" onClick={onOpen}><span><strong>{categoryLabel(t, finding.category)}</strong><small>{finding.explanation}</small></span><b>{finding.count}</b><em aria-hidden="true">→</em></button>;
}

function ScoutGoonLeadersView({ page, hours, onNavigate }: { page: ScoutGoonLeadersPage; hours: ScoutHours; onNavigate: (route: ScoutRoute) => void }) {
  const { t } = useTranslation();
  const rows = page.items ?? [];
  const first = page.total === 0 ? 0 : page.offset + 1;
  const last = Math.min(page.offset + rows.length, page.total);
  const previousOffset = Math.max(0, page.offset - page.limit);
  const nextOffset = page.offset + page.limit;
  return <section className="panel"><header className="section-heading"><div><span className="eyebrow">{t("scout.goonLeaders")}</span><h2>{t("scout.goonLeaders")}</h2><p>{t("scout.sharedGoons")}</p></div><small>{t("scout.sortedByCpu")} · {t("scout.resultRange", { first, last, total: page.total })}</small></header>{rows.length === 0 ? <p className="empty-inline">{t("scout.none")}</p> : <div className="table-shell"><table><thead><tr><th>#</th><th>{t("scout.goon")}</th><th>{t("scout.cpu")}</th><th>{t("scout.runs")}</th><th>{t("scout.medianCpuRun")}</th><th>{t("scout.peakMemoryShort")}</th><th>{t("scout.cpuCoverage")}</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.key}-${row.goon_source_sha256 ?? "all"}`}><td>{page.offset + index + 1}</td><td><button type="button" className="text-button" onClick={() => row.goon_name && onNavigate({ view: "goon", hours, goonName: row.goon_name, ...(row.goon_source_sha256 ? { sourceSha256: row.goon_source_sha256 } : {}) })}><strong>{row.display_name}</strong></button>{row.goon_source_sha256 ? <small>{shortSha(row.goon_source_sha256)}</small> : null}</td><td>{formatCpu(row.total_cpu_seconds)}</td><td>{formatCount(row.run_count)}</td><td>{formatCpu(row.median_cpu_seconds_per_run)}{row.median_duration_seconds_per_run != null ? <small>{t("scout.medianDuration")}: {formatDuration(row.median_duration_seconds_per_run)}</small> : null}{row.median_average_cpu_cores != null ? <small>{t("scout.medianAverageCpu")}: {formatCores(row.median_average_cpu_cores)}</small> : null}</td><td>{formatBytes(row.peak_memory_bytes)}</td><td>{formatCoverage(row.cpu_coverage)}</td></tr>)}</tbody></table></div>}<footer className="button-row"><button type="button" className="text-button" disabled={page.offset === 0} onClick={() => onNavigate({ view: "goon-leaders", hours, ...(previousOffset ? { offset: previousOffset } : {}) })}>← {t("scout.previous")}</button><button type="button" className="text-button" disabled={nextOffset >= page.total} onClick={() => onNavigate({ view: "goon-leaders", hours, offset: nextOffset })}>{t("scout.next")} →</button></footer></section>;
}

function ScoutFindingsView({ page, category, hours, onNavigate, onOpenRun }: { page: ScoutFindingsPage; category: ScoutFinding["category"]; hours: ScoutHours; onNavigate: (route: ScoutRoute) => void; onOpenRun: (runId: string) => void }) {
  const { t } = useTranslation();
  const data = page.items ?? [];
  const first = page.total === 0 ? 0 : page.offset + 1;
  const last = Math.min(page.offset + data.length, page.total);
  const previousOffset = Math.max(0, page.offset - page.limit);
  const nextOffset = page.offset + page.limit;
  const hasPrevious = page.offset > 0;
  const hasNext = nextOffset < page.total;
  return <section className="panel"><header className="section-heading"><div><span className="eyebrow">{t("scout.findings")}</span><h2>{categoryLabel(t, category)}</h2><p>{findingSortLabel(t, category)}</p></div><small>{t("scout.resultRange", { first, last, total: page.total })}</small></header>{data.length === 0 ? <p className="empty-inline">{t("scout.none")}</p> : <div className="compact-card-list">{data.map((finding, index) => <article className="panel" key={`${finding.category}-${finding.repository_id ?? finding.goon_name ?? finding.run_id ?? index}`}><div className="scout-finding__main"><span className="eyebrow">{t("scout.rankOf", { rank: page.offset + index + 1, total: page.total })}</span><strong>{finding.repository_name ?? finding.goon_name ?? finding.step_id ?? categoryLabel(t, finding.category)}</strong><span>{[finding.goon_name, finding.step_id].filter(Boolean).join(" / ")}</span><p>{finding.explanation}</p></div><dl className="summary-grid summary-grid--compact"><MetricFact label={t("scout.observedEvidence")} value={formatFindingValue(finding.observed, finding.unit)} /><MetricFact label={t("scout.baseline")} value={finding.baseline == null ? t("scout.missing") : formatFindingValue(finding.baseline, finding.unit)} /><MetricFact label={t("scout.ratio")} value={finding.ratio == null ? "—" : `${finding.ratio.toFixed(1)}×`} /><MetricFact label={findingSampleLabel(t, finding.category)} value={formatCount(finding.sample_count ?? 0)} /></dl><FindingEvidenceDetails finding={finding} window={page.window} /><div className="button-row">{finding.repository_id ? <button type="button" className="text-button" onClick={() => onNavigate({ view: "repository", hours, repositoryId: finding.repository_id! })}>{t("scout.repository")} →</button> : null}{finding.goon_name ? <button type="button" className="text-button" onClick={() => onNavigate({ view: "goon", hours, goonName: finding.goon_name!, ...(finding.goon_source_sha256 ? { sourceSha256: finding.goon_source_sha256 } : {}) })}>{t("scout.goon")} →</button> : null}{finding.representative_run_id ? <button type="button" className="button button--secondary" onClick={() => onOpenRun(finding.representative_run_id!)}>{t("scout.openGameTape")}</button> : null}</div></article>)}</div>}<footer className="button-row"><button type="button" className="text-button" disabled={!hasPrevious} onClick={() => onNavigate({ view: "findings", hours, category, ...(previousOffset ? { offset: previousOffset } : {}) })}>← {t("scout.previous")}</button><button type="button" className="text-button" disabled={!hasNext} onClick={() => onNavigate({ view: "findings", hours, category, offset: nextOffset })}>{t("scout.next")} →</button></footer></section>;
}

function ScoutRepositoryView({ data, hours, onNavigate, onOpenRun }: { data: ScoutRepositoryDetail; hours: ScoutHours; onNavigate: (route: ScoutRoute) => void; onOpenRun: (runId: string) => void }) {
  const { t } = useTranslation();
  const row = data.repository;
  const plans = data.plans ?? [];
  const page = (section: ScoutPageSection) => ({ offset: data.page_section === section ? data.page_offset : 0, limit: data.page_limit });
  const base = { view: "repository", hours, repositoryId: row.repository_id!, ...(data.selected_plan_id ? { planId: data.selected_plan_id } : {}) } as const;
  return <><section className="panel"><span className="eyebrow">{t("scout.repository")}</span><h2>{row.display_name}</h2>{data.selected_plan_id ? <p>{t("scout.selectedPlan")}: <strong>{plans.find((plan) => plan.plan_id === data.selected_plan_id)?.plan_name ?? data.selected_plan_id}</strong></p> : <p>{t("scout.allPlans")}</p>}<div className="summary-grid"><MiniKpi label={t("scout.totalCpu")} value={formatCpu(row.total_cpu_seconds)} hint={coverageText(t, row.cpu_coverage)} /><MiniKpi label={t("scout.runs")} value={formatCount(row.run_count)} hint={`${formatCount(row.attempt_count)} ${t("scout.attempts").toLowerCase()}`} /><MiniKpi label={t("scout.medianCpuRun")} value={formatCpu(row.median_cpu_seconds_per_run)} hint={t("scout.observedEvidence")} /><MiniKpi label={t("scout.medianDuration")} value={formatDuration(row.median_duration_seconds_per_run)} hint={t("scout.wallDurationHint")} /><MiniKpi label={t("scout.medianAverageCpu")} value={formatCores(row.median_average_cpu_cores)} hint={t("scout.averageCpuHint")} /><MiniKpi label={t("scout.peakMemory")} value={formatBytes(row.peak_memory_bytes)} hint={coverageText(t, row.memory_coverage)} /><MiniKpi label={t("scout.failedCpu")} value={formatCpu(row.failed_cpu_seconds)} hint={t("scout.failedCpuHint")} /><MiniKpi label={t("scout.cancelledCpu")} value={formatCpu(row.cancelled_cpu_seconds)} hint={t("scout.cancelledCpuHint")} /></div></section><PlanPanel plans={plans} total={data.plan_total} {...page("plans")} onPage={(offset) => onNavigate({ ...base, section: "plans", ...(offset ? { offset } : {}) })} {...(data.selected_plan_id ? { selectedPlanId: data.selected_plan_id } : {})} onSelect={(planId) => onNavigate({ view: "repository", hours, repositoryId: row.repository_id!, ...(planId ? { planId } : {}) })} /><RankingPanel title={t("scout.whereResourcesWent")} description={t("scout.sortedByCpu")} rows={data.goons ?? []} total={data.goon_total} {...page("goons")} onPage={(offset) => onNavigate({ ...base, section: "goons", ...(offset ? { offset } : {}) })} onOpen={(goon) => goon.goon_name && onNavigate({ view: "goon", hours, goonName: goon.goon_name, ...(goon.goon_source_sha256 ? { sourceSha256: goon.goon_source_sha256 } : {}) })} /><StepPanel steps={data.steps ?? []} total={data.step_total} {...page("steps")} onPage={(offset) => onNavigate({ ...base, section: "steps", ...(offset ? { offset } : {}) })} onOpenRun={onOpenRun} /><RunSamples samples={data.representative_runs ?? []} onOpenRun={onOpenRun} /></>;
}


function ScoutGoonView({ data, hours, trendMetric, onTrendMetric, onNavigate, onOpenRun }: { data: ScoutGoonDetail; hours: ScoutHours; trendMetric: TrendMetric; onTrendMetric: (metric: TrendMetric) => void; onNavigate: (route: ScoutRoute) => void; onOpenRun: (runId: string) => void }) {
  const { t } = useTranslation();
  const page = (section: ScoutPageSection) => ({ offset: data.page_section === section ? data.page_offset : 0, limit: data.page_limit });
  const base = { view: "goon", hours, goonName: data.goon_name, ...(data.selected_source_sha256 ? { sourceSha256: data.selected_source_sha256 } : {}) } as const;
  const versionPage = page("versions");
  return <><section className="panel"><span className="eyebrow">{t("scout.goon")}</span><h2>{data.goon_name}</h2><p>{data.selected_source_sha256 ? `${t("scout.selectedVersion")}: ${shortSha(data.selected_source_sha256)}` : t("scout.allVersions")}</p></section><section className="panel"><header className="section-heading"><div><span className="eyebrow">{t("scout.goonTrend")}</span><h2>{t("scout.goonTrend")}</h2></div><TrendMetricSelector metric={trendMetric} onChange={onTrendMetric} /></header><ScoutTrend points={data.trend ?? []} metric={trendMetric} /></section><section className="panel"><header className="section-heading"><h2>{t("scout.versions")}</h2><small>{countDisclosure(t, data.versions?.length ?? 0, data.version_total)}</small></header><div className="table-shell"><table><thead><tr><th>{t("scout.source")}</th><th>{t("scout.repositories")}</th><th>{t("scout.runs")}</th><th>{t("scout.cpu")}</th><th>{t("scout.medianCpuRun")}</th><th>{t("scout.firstSeen")}</th><th>{t("scout.lastSeen")}</th></tr></thead><tbody>{(data.versions ?? []).map((version) => <tr key={version.source_sha256}><td><button type="button" className="text-button" onClick={() => onNavigate({ view: "goon", hours, goonName: data.goon_name, sourceSha256: version.source_sha256 })}>{shortSha(version.source_sha256)}</button></td><td>{version.repository_count}</td><td>{version.run_count}</td><td>{formatCpu(version.total_cpu_seconds)}</td><td>{formatCpu(version.median_cpu_seconds_per_run)}</td><td>{formatDate(version.first_seen_at)}</td><td>{formatDate(version.last_seen_at)}</td></tr>)}</tbody></table></div><ScoutListPager offset={versionPage.offset} limit={versionPage.limit} shown={data.versions?.length ?? 0} total={data.version_total} onPage={(offset) => onNavigate({ ...base, section: "versions", ...(offset ? { offset } : {}) })} /></section><RankingPanel title={t("scout.repositories")} description={t("scout.sortedByCpu")} rows={data.repositories ?? []} total={data.repository_total} {...page("repositories")} onPage={(offset) => onNavigate({ ...base, section: "repositories", ...(offset ? { offset } : {}) })} onOpen={(row) => row.repository_id && onNavigate({ view: "repository", hours, repositoryId: row.repository_id })} /><StepPanel steps={data.steps ?? []} total={data.step_total} {...page("steps")} onPage={(offset) => onNavigate({ ...base, section: "steps", ...(offset ? { offset } : {}) })} onOpenRun={onOpenRun} /><RunSamples samples={data.representative_runs ?? []} onOpenRun={onOpenRun} /></>;
}



function PlanPanel({ plans, total, offset = 0, limit = 100, selectedPlanId, onPage, onSelect }: { plans: ScoutPlanSummary[]; total?: number; offset?: number; limit?: number; selectedPlanId?: string; onPage?: (offset: number) => void; onSelect: (planId?: string) => void }) {
  const { t } = useTranslation();
  return <section className="panel"><header className="section-heading"><div><h2>{t("scout.plans")}</h2><p>{t("scout.planDrillHint")} · {countDisclosure(t, plans.length, total ?? plans.length)}</p></div>{selectedPlanId ? <button type="button" className="text-button" onClick={() => onSelect()}>{t("scout.allPlans")} →</button> : null}</header>{plans.length === 0 ? <p className="empty-inline">{t("scout.none")}</p> : <div className="table-shell"><table><thead><tr><th>{t("scout.plan")}</th><th>{t("scout.cpu")}</th><th>{t("scout.runs")}</th><th>{t("scout.medianCpuRun")}</th><th>{t("scout.peakMemoryShort")}</th><th>{t("scout.cpuCoverage")}</th></tr></thead><tbody>{plans.map((plan) => <tr key={plan.plan_id} aria-current={selectedPlanId === plan.plan_id ? "true" : undefined}><td><button type="button" className="text-button" onClick={() => onSelect(plan.plan_id)}><strong>{plan.plan_name}</strong></button><small>{plan.plan_id}</small></td><td>{formatCpu(plan.total_cpu_seconds)}</td><td>{formatCount(plan.run_count)}</td><td>{formatCpu(plan.median_cpu_seconds_per_run)}{plan.median_duration_seconds_per_run != null ? <small>{t("scout.medianDuration")}: {formatDuration(plan.median_duration_seconds_per_run)}</small> : null}{plan.median_average_cpu_cores != null ? <small>{t("scout.medianAverageCpu")}: {formatCores(plan.median_average_cpu_cores)}</small> : null}</td><td>{formatBytes(plan.peak_memory_bytes)}</td><td>{formatCoverage(plan.cpu_coverage)}</td></tr>)}</tbody></table></div>}<ScoutListPager offset={offset} limit={limit} shown={plans.length} total={total ?? plans.length} onPage={onPage} /></section>;
}

function RankingPanel({ title, description, rows, total, offset = 0, limit = 100, onPage, onOpen }: { title: string; description: string; rows: ScoutRankingRow[]; total?: number; offset?: number; limit?: number; onPage?: (offset: number) => void; onOpen: (row: ScoutRankingRow) => void }) {
  const { t } = useTranslation();
  return <section className="panel"><header className="section-heading"><div><h2>{title}</h2><p>{description}</p></div><small>{t("scout.sortedByCpu")} · {countDisclosure(t, rows.length, total ?? rows.length)}</small></header>{rows.length === 0 ? <p className="empty-inline">{t("scout.none")}</p> : <div className="table-shell"><table><thead><tr><th>#</th><th>{rows[0]?.entity_kind === "GOON" ? t("scout.goon") : t("scout.repository")}</th><th>{t("scout.cpu")}</th><th>{t("scout.runs")}</th><th>{t("scout.medianCpuRun")}</th><th>{t("scout.peakMemoryShort")}</th><th>{t("scout.cpuCoverage")}</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.entity_kind}-${row.key}`}><td>{offset + index + 1}</td><td><button type="button" className="text-button" onClick={() => onOpen(row)}><strong>{row.display_name}</strong></button>{row.goon_source_sha256 ? <small>{shortSha(row.goon_source_sha256)}</small> : null}</td><td>{formatCpu(row.total_cpu_seconds)}</td><td>{formatCount(row.run_count)}</td><td>{formatCpu(row.median_cpu_seconds_per_run)}{row.median_duration_seconds_per_run != null ? <small>{t("scout.medianDuration")}: {formatDuration(row.median_duration_seconds_per_run)}</small> : null}{row.median_average_cpu_cores != null ? <small>{t("scout.medianAverageCpu")}: {formatCores(row.median_average_cpu_cores)}</small> : null}</td><td>{formatBytes(row.peak_memory_bytes)}</td><td>{formatCoverage(row.cpu_coverage)}</td></tr>)}</tbody></table></div>}<ScoutListPager offset={offset} limit={limit} shown={rows.length} total={total ?? rows.length} onPage={onPage} /></section>;
}

function StepPanel({ steps, total, offset = 0, limit = 100, onPage, onOpenRun }: { steps: ScoutStepSummary[]; total?: number; offset?: number; limit?: number; onPage?: (offset: number) => void; onOpenRun: (runId: string) => void }) {
  const { t } = useTranslation();
  return <section className="panel"><header className="section-heading"><h2>{t("scout.steps")}</h2><small>{countDisclosure(t, steps.length, total ?? steps.length)}</small></header>{steps.length === 0 ? <p className="empty-inline">{t("scout.none")}</p> : <div className="table-shell"><table><thead><tr><th>{t("scout.step")}</th><th>{t("scout.cpu")}</th><th>{t("scout.medianCpuRun")}</th><th>{t("scout.peerMedian")}</th><th>{t("scout.ratio")}</th><th>{t("scout.peakMemoryShort")}</th><th>{t("scout.runs")}</th><th /></tr></thead><tbody>{steps.map((step) => {
    const comparisonReady = step.cpu_ratio_to_peer_median != null;
    const peerEvidence = (step.peer_execution_count ?? 0) > 0
      ? `${t("scout.peerEvidence", { executions: step.peer_execution_count, repositories: step.peer_repository_count })} · ${t("scout.cpuCoverage")}: ${step.peer_cpu_coverage ? formatCoverage(step.peer_cpu_coverage) : "—"}`
      : t("scout.noPeerEvidence");
    return <tr key={`${step.goon_source_sha256}-${step.step_id}`}><td><strong>{step.step_id}</strong><small>{step.goon_name} · {shortSha(step.goon_source_sha256)}</small></td><td>{formatCpu(step.total_cpu_seconds)}</td><td>{formatCpu(step.median_cpu_seconds_per_run)}<small>{t("scout.medianDuration")}: {formatDuration(step.median_duration_seconds_per_run)}</small>{step.median_average_cpu_cores != null ? <small>{t("scout.medianAverageCpu")}: {formatCores(step.median_average_cpu_cores)}</small> : null}</td><td>{formatCpu(step.peer_median_cpu_seconds_per_run)}<small>{comparisonReady ? peerEvidence : `${t("scout.notEnoughComparableData")} · ${peerEvidence}`}</small>{step.comparison_environment ? <EnvironmentEvidence environment={step.comparison_environment} /> : null}</td><td>{comparisonReady ? `${step.cpu_ratio_to_peer_median!.toFixed(1)}×` : "—"}{step.duration_ratio_to_peer_median != null ? <small>{t("scout.durationPeerEvidence", { median: formatDuration(step.peer_median_duration_seconds_per_run), ratio: step.duration_ratio_to_peer_median.toFixed(1) })}</small> : null}</td><td>{formatBytes(step.peak_memory_bytes)}{step.memory_ratio_to_peer_median != null ? <small>{t("scout.memoryPeerEvidence", { median: formatBytes(step.peer_median_peak_memory_bytes), ratio: step.memory_ratio_to_peer_median.toFixed(1), coverage: step.peer_memory_coverage ? formatCoverage(step.peer_memory_coverage) : "—" })}</small> : step.peer_memory_coverage && step.peer_memory_coverage.eligible > 0 ? <small>{t("scout.memoryComparisonUnavailable", { coverage: formatCoverage(step.peer_memory_coverage) })}</small> : null}</td><td>{step.run_count}</td><td>{step.representative_run_id ? <button type="button" className="text-button" onClick={() => onOpenRun(step.representative_run_id!)}>{t("scout.openGameTape")}</button> : null}</td></tr>;
  })}</tbody></table></div>}<ScoutListPager offset={offset} limit={limit} shown={steps.length} total={total ?? steps.length} onPage={onPage} /></section>;
}


function ScoutListPager({ offset, limit, shown, total, onPage }: { offset: number; limit: number; shown: number; total: number; onPage?: (offset: number) => void }) {
  const { t } = useTranslation();
  if (!onPage || (offset === 0 && shown >= total)) return null;
  const nextOffset = offset + limit;
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + shown, total);
  return <nav className="button-row" aria-label={t("scout.browseMore")}><button type="button" className="text-button" disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - limit))}>← {t("scout.previous")}</button><span>{t("scout.resultRange", { first, last, total })}</span><button type="button" className="text-button" disabled={nextOffset >= total} onClick={() => onPage(nextOffset)}>{t("scout.next")} →</button></nav>;
}

function RunSamples({ samples, onOpenRun }: { samples: ScoutRunSample[]; onOpenRun: (runId: string) => void }) {
  const { t } = useTranslation();
  return <section className="panel"><header><h2>{t("scout.representativeRuns")}</h2></header>{samples.length === 0 ? <p className="empty-inline">{t("scout.none")}</p> : <div className="compact-card-list">{samples.map((sample) => <article className="compact-card" key={sample.run_id}><div><strong>{sample.run_id}</strong><span>{sample.goon_name && sample.step_id ? `${sample.goon_name} / ${sample.step_id}` : String(sample.status)}</span><small>{formatDate(sample.started_at)} · {formatDuration(sample.duration_seconds)} · {formatCpu(sample.cpu_seconds)} · {formatCores(sample.average_cpu_cores)} · {formatBytes(sample.peak_memory_bytes)}</small></div><button type="button" className="button button--secondary" onClick={() => onOpenRun(sample.run_id)}>{t("scout.openGameTape")}</button></article>)}</div>}</section>;
}

function TrendMetricSelector({ metric, onChange }: { metric: TrendMetric; onChange: (metric: TrendMetric) => void }) {
  const { t } = useTranslation();
  return <div className="segmented-control" role="group" aria-label={t("scout.trend")}><button type="button" className={metric === "cpu" ? "segmented-control__item segmented-control__item--active" : "segmented-control__item"} aria-pressed={metric === "cpu"} onClick={() => onChange("cpu")}>{t("scout.trendCpu")}</button><button type="button" className={metric === "memory" ? "segmented-control__item segmented-control__item--active" : "segmented-control__item"} aria-pressed={metric === "memory"} onClick={() => onChange("memory")}>{t("scout.trendMemory")}</button><button type="button" className={metric === "runs" ? "segmented-control__item segmented-control__item--active" : "segmented-control__item"} aria-pressed={metric === "runs"} onClick={() => onChange("runs")}>{t("scout.trendRuns")}</button></div>;
}


function ScoutTrend({ points, metric }: { points: ScoutTrendPoint[]; metric: TrendMetric }) {
  const { t } = useTranslation();
  if (points.length === 0) return <p className="empty-inline">{t("scout.noTrend")}</p>;
  const values: Array<number | null> = points.map((point) => metric === "cpu" ? point.total_cpu_seconds ?? null : metric === "runs" ? point.run_count : point.peak_memory_bytes ?? null);
  const observedValues = values.filter((value): value is number => value != null);
  const max = Math.max(...observedValues, 1);
  const width = 600; const height = 150; const padding = 12;
  const segments = trendSegments(values, width, height, padding, max);
  const label = metric === "cpu" ? t("scout.trendCpu") : metric === "runs" ? t("scout.trendRuns") : t("scout.trendMemory");
  const firstPoint = points[0]!;
  const lastPoint = points[points.length - 1]!;
  const latestValue = [...values].reverse().find((value) => value != null) ?? null;
  return <div><svg viewBox={`0 0 ${width} ${height}`} width="100%" height="180" role="img" aria-label={`${label}: ${values.map((value) => formatTrendValue(value, metric)).join(", ")}`} preserveAspectRatio="none"><line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} stroke="var(--border-strong)" strokeWidth="1" />{segments.map((coordinates, index) => <polyline key={index} points={coordinates} fill="none" stroke="var(--status-info)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />)}</svg><div className="field-heading"><span className="muted">{formatDate(firstPoint.start_at)}</span><strong>{formatTrendValue(latestValue, metric)}</strong><span className="muted">{formatDate(lastPoint.end_at)}</span></div></div>;
}

function trendSegments(values: Array<number | null>, width: number, height: number, padding: number, max: number): string[] {
  const xFor = (index: number) => values.length === 1 ? width / 2 : padding + index * ((width - padding * 2) / (values.length - 1));
  const yFor = (value: number) => height - padding - (value / max) * (height - padding * 2);
  const segments: string[][] = [];
  let current: string[] = [];
  values.forEach((value, index) => {
    if (value == null) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    current.push(`${xFor(index).toFixed(1)},${yFor(value).toFixed(1)}`);
  });
  if (current.length) segments.push(current);
  return segments.map((segment) => segment.join(" "));
}


function EnvironmentEvidence({ environment }: { environment: ScoutComparisonEnvironment }) {
  const { t } = useTranslation();
  const envelopes = environment.resource_envelopes ?? [];
  const envelopeText = envelopes.length === 0
    ? t("scout.environmentUnknown")
    : envelopes.map((item) => `${formatCores(item.cpu_millis / 1000)} · ${formatBytes(item.memory_bytes)}`).join("; ");
  return <details><summary>{t("scout.environmentEvidence")}</summary><dl className="summary-grid summary-grid--compact"><MetricFact label={t("scout.resourceEnvelope")} value={envelopeText} /><MetricFact label={t("scout.isolationBackend")} value={environment.isolation_backend_id ?? t("scout.unknown")} /><MetricFact label={t("scout.isolationConfiguration")} value={environment.isolation_configuration_id ?? t("scout.unknown")} /><MetricFact label={t("scout.runtimeRoot")} value={environment.runtime_root_sha256 ? shortSha(environment.runtime_root_sha256) : t("scout.unknown")} /><MetricFact label={t("scout.hostToolchain")} value={environment.host_toolchain_id ?? t("scout.unknown")} /></dl></details>;
}


function FindingEvidenceDetails({ finding, window }: { finding: ScoutFinding; window: ScoutFindingsPage["window"] }) {
  const { t } = useTranslation();
  const comparison = finding.ratio != null || finding.baseline != null;
  const compatibility = finding.category === "RUN_SPIKE" ? t("scout.compatibilityHistorical") : comparison ? t("scout.compatibilityPeers") : t("scout.compatibilityRanking");
  const population = finding.category === "CPU_OUTLIER" || finding.category === "MEMORY_OUTLIER" || finding.category === "GOON_REGRESSION"
    ? t("scout.populationSuccessful")
    : finding.category === "FAILURE_WASTE" ? t("scout.populationUnsuccessful") : t("scout.populationAll");
  return <details><summary>{t("scout.evidenceDetails")}</summary><dl className="summary-grid summary-grid--compact"><MetricFact label={t("scout.metric")} value={`${finding.metric} · ${finding.unit}`} /><MetricFact label={t("scout.window")} value={`${formatDate(window.start_at)} → ${formatDate(window.end_at)}`} /><MetricFact label={t("scout.outcomePopulation")} value={population} /><MetricFact label={t("scout.comparisonCompatibility")} value={compatibility} />{finding.current_repository_count ? <MetricFact label={t("scout.currentRepositories")} value={formatCount(finding.current_repository_count)} /> : null}{finding.baseline_repository_count ? <MetricFact label={t("scout.baselineRepositories")} value={formatCount(finding.baseline_repository_count)} /> : null}{finding.overlap_repository_count ? <MetricFact label={t("scout.overlapRepositories")} value={formatCount(finding.overlap_repository_count)} /> : null}{finding.peer_repository_count && !finding.overlap_repository_count ? <MetricFact label={t("scout.peerRepositories")} value={formatCount(finding.peer_repository_count)} /> : null}{finding.sample_count ? <MetricFact label={findingSampleLabel(t, finding.category)} value={formatCount(finding.sample_count)} /> : null}{finding.coverage ? <MetricFact label={t("scout.telemetryCoverage")} value={formatCoverage(finding.coverage)} /> : null}{finding.failed_cpu_seconds != null ? <MetricFact label={t("scout.failedCpu")} value={formatCpu(finding.failed_cpu_seconds)} /> : null}{finding.cancelled_cpu_seconds != null ? <MetricFact label={t("scout.cancelledCpu")} value={formatCpu(finding.cancelled_cpu_seconds)} /> : null}{finding.goon_source_sha256 ? <MetricFact label={t("scout.currentVersion")} value={shortSha(finding.goon_source_sha256)} /> : null}{finding.baseline_goon_source_sha256 ? <MetricFact label={t("scout.baselineVersion")} value={shortSha(finding.baseline_goon_source_sha256)} /> : null}{finding.current_period_start_at && finding.current_period_end_at ? <MetricFact label={t("scout.currentPeriod")} value={`${formatDate(finding.current_period_start_at)} → ${formatDate(finding.current_period_end_at)}`} /> : null}{finding.baseline_period_start_at && finding.baseline_period_end_at ? <MetricFact label={t("scout.baselinePeriod")} value={`${formatDate(finding.baseline_period_start_at)} → ${formatDate(finding.baseline_period_end_at)}`} /> : null}</dl>{finding.comparison_environment ? <EnvironmentEvidence environment={finding.comparison_environment} /> : null}</details>;
}

function MiniKpi({ label, value, hint }: { label: string; value: string; hint: string }) { return <article className="summary-card"><span>{label}</span><strong>{value}</strong><small>{hint}</small></article>; }
function MetricFact({ label, value }: { label: string; value: string }) { return <div className="summary-card"><dt>{label}</dt><dd>{value}</dd></div>; }
type ScoutTranslate = (key: string, options?: Record<string, unknown>) => string;
function categoryLabel(t: ScoutTranslate, category: ScoutFinding["category"]): string { return t(`scout.categories.${category}`); }
function findingSortLabel(t: ScoutTranslate, category: ScoutFindingCategory): string {
  const key = category === "MOST_ACTIVE" ? "runCount" : category === "EXPENSIVE_RUN" ? "cpuPerRun" : category === "FAILURE_WASTE" ? "wastedCpu" : category === "HEAVY_HITTER" ? "totalCpu" : "comparisonRatio";
  return t("scout.sortedBy", { measure: t(`scout.sortMeasures.${key}`) });
}
function findingSampleLabel(t: ScoutTranslate, category: ScoutFindingCategory): string {
  if (category === "CPU_OUTLIER" || category === "MEMORY_OUTLIER") return t("scout.peerExecutions");
  if (category === "RUN_SPIKE") return t("scout.baselinePeriods");
  if (category === "FAILURE_WASTE") return t("scout.unsuccessfulExecutions");
  if (category === "GOON_REGRESSION") return t("scout.currentSourceExecutions");
  return t("scout.executions");
}
function countDisclosure(t: ScoutTranslate, shown: number, total: number): string { return t("scout.showingCount", { shown: formatCount(shown), total: formatCount(total) }); }
function formatCount(value: number): string { return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value); }
function formatCpu(value: number | null | undefined): string { if (value == null) return "—"; if (value >= 3600) return `${(value / 3600).toFixed(value >= 36000 ? 0 : 1)} CPU-h`; if (value >= 60) return `${(value / 60).toFixed(1)} CPU-min`; return `${value.toFixed(value >= 10 ? 1 : 2)} CPU-s`; }
function formatCores(value: number | null | undefined): string { if (value == null) return "—"; return `${value.toFixed(value >= 10 ? 1 : 2)} cores`; }
function formatBytes(value: number | null | undefined): string { if (value == null) return "—"; const gib = value / (1024 ** 3); return gib >= 1 ? `${gib.toFixed(gib >= 10 ? 1 : 2)} GiB` : `${(value / (1024 ** 2)).toFixed(0)} MiB`; }
function formatStorageBytes(value: number): string { if (value >= 1024 ** 3) return `${(value / (1024 ** 3)).toFixed(2)} GiB`; if (value >= 1024 ** 2) return `${(value / (1024 ** 2)).toFixed(1)} MiB`; return `${Math.ceil(value / 1024)} KiB`; }
function formatDuration(value: number | null | undefined): string { if (value == null) return "—"; if (value >= 3600) return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`; if (value >= 60) return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`; if (value >= 1) return `${value.toFixed(value >= 10 ? 0 : 1)}s`; return `${Math.round(value * 1000)}ms`; }
function formatCoverage(coverage: ScoutCoverage): string { return `${coverage.percent.toFixed(coverage.percent === 100 ? 0 : 1)}%`; }
function coverageText(t: (key: string, options?: Record<string, unknown>) => string, coverage: ScoutCoverage): string { return t("scout.coverage", { percent: formatCoverage(coverage).replace("%", "") }); }
function shortSha(value: string): string { return value.slice(0, 10); }
function formatDate(value: string): string { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function formatFindingValue(value: number, unit: ScoutFinding["unit"]): string { if (unit === "CPU-s") return formatCpu(value); if (unit === "bytes") return formatBytes(value); return `${formatCount(value)} ${unit}`; }
function formatTrendValue(value: number | null, metric: TrendMetric): string { if (value == null) return "—"; return metric === "cpu" ? formatCpu(value) : metric === "memory" ? formatBytes(value) : formatCount(value); }
