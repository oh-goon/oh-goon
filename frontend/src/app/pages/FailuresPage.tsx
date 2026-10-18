import { useTranslation } from "react-i18next";

import { queryParamsFromHash, type AppPage } from "@/app/navigation";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorBanner } from "@/components/common/ErrorBanner";
import { PageHeading } from "@/components/common/PageHeading";
import { useFailureGroups, useFailureInventory, useFailureRepositories } from "@/features/failures/FailuresFeature";

interface Props {
  navigate(page: AppPage, query?: Readonly<Record<string, string>>): void;
}

const PAGE_SIZE = 50;

export function FailuresPage({ navigate }: Props) {
  const { t } = useTranslation();
  const state = readFailureRoute();
  const inventory = useFailureInventory();
  const groups = useFailureGroups(state.query, state.groupOffset, PAGE_SIZE);
  const repositories = useFailureRepositories(
    state.groupId,
    state.repositoryQuery,
    state.signatureId,
    state.repositoryOffset,
    PAGE_SIZE,
  );
  const groupItems = groups.resource.data?.items ?? [];
  const repositoryItems = repositories.resource.data?.items ?? [];
  const error = inventory.resource.status === "read-error"
    ? inventory.resource.failure?.message ?? null
    : groups.resource.status === "read-error"
      ? groups.resource.failure?.message ?? null
      : repositories.resource.status === "read-error"
        ? repositories.resource.failure?.message ?? null
        : null;

  const update = (patch: Partial<FailureRouteState>) => navigate("failures", failureRouteQuery({ ...state, ...patch }));
  const selectGroup = (groupId: string) => update({ groupId, repositoryOffset: 0, repositoryQuery: "", signatureId: null });
  const changeGroupPage = (groupOffset: number) => update({
    groupOffset,
    groupId: null,
    repositoryQuery: "",
    repositoryOffset: 0,
    signatureId: null,
  });

  return <main className="page failures-page">
    <PageHeading eyebrow={t("failures.eyebrow")} title={t("failures.title")} description={t("failures.description")} />
    {error ? <ErrorBanner message={error} /> : null}

    <section className="panel failure-overview">
      <div className="section-heading"><div><h2>{t("failures.currentTitle")}</h2><p>{t("failures.currentDescription")}</p></div></div>
      {inventory.resource.data ? <div className="summary-grid">
        <div className="summary-card"><span>{t("failures.repositories")}</span><strong>{inventory.resource.data.failing_repositories.toLocaleString()}</strong></div>
        <div className="summary-card"><span>{t("failures.groups")}</span><strong>{inventory.resource.data.failure_groups.toLocaleString()}</strong></div>
      </div> : <p className="muted loading-state" role="status">{t("failures.loading")}</p>}
    </section>

    <section className="panel failure-workspace failure-workspace--groups" aria-label={t("failures.groupTitle")}>
      <div className="section-heading"><div><h2>{t("failures.groupTitle")}</h2><p>{t("failures.groupDescription")}</p></div>{groups.resource.data ? <small>{t("failures.resultRange", { first: groups.resource.data.total === 0 ? 0 : groups.resource.data.offset + 1, last: Math.min(groups.resource.data.total, groups.resource.data.offset + groupItems.length), total: groups.resource.data.total })}</small> : null}</div>
      <label className="field failure-search"><span>{t("failures.searchGroups")}</span><input type="search" value={state.query} onChange={(event) => update({ query: event.target.value, groupOffset: 0, groupId: null, repositoryOffset: 0 })} placeholder={t("failures.searchGroupsPlaceholder")} /></label>
      {groupItems.length === 0 && groups.resource.status !== "read-loading" ? <EmptyState title={t("failures.noGroupsTitle")} message={t("failures.noGroupsMessage")} /> : null}
      {groupItems.length > 0 ? <div className="table-shell"><table><thead><tr><th>{t("failures.plan")}</th><th>{t("failures.goon")}</th><th>{t("failures.step")}</th><th>{t("failures.affected")}</th><th>{t("failures.signatures")}</th></tr></thead><tbody>{groupItems.map((group) => <tr key={group.group_id} aria-current={state.groupId === group.group_id ? "true" : undefined}><td><button className="text-button" type="button" onClick={() => selectGroup(group.group_id)}><strong>{group.plan_name}</strong></button><small>{group.plan_id}</small></td><td>{group.goon_name ?? t("failures.repositoryLevel")}</td><td>{group.step_id ?? t("failures.repositoryLevel")}</td><td>{group.affected_repositories.toLocaleString()}</td><td>{group.signature_count.toLocaleString()}</td></tr>)}</tbody></table></div> : null}
      <div className="button-row">
        <button className="text-button" type="button" disabled={state.groupOffset === 0} onClick={() => changeGroupPage(Math.max(0, state.groupOffset - PAGE_SIZE))}>← {t("failures.previous")}</button>
        <button className="text-button" type="button" disabled={!groups.resource.data || state.groupOffset + PAGE_SIZE >= groups.resource.data.total} onClick={() => changeGroupPage(state.groupOffset + PAGE_SIZE)}>{t("failures.next")} →</button>
      </div>
    </section>

    {state.groupId ? <section className="panel failure-workspace failure-workspace--repositories" id="affected-repositories" aria-label={t("failures.repositoryTitle")}>
      <div className="section-heading"><div><h2>{t("failures.repositoryTitle")}</h2><p>{repositories.resource.data ? t("failures.repositoryDescription", { plan: repositories.resource.data.group.plan_name, goon: repositories.resource.data.group.goon_name ?? t("failures.repositoryLevel"), step: repositories.resource.data.group.step_id ?? t("failures.repositoryLevel") }) : t("failures.loading")}</p></div>{repositories.resource.data ? <small>{t("failures.resultRange", { first: repositories.resource.data.total === 0 ? 0 : repositories.resource.data.offset + 1, last: Math.min(repositories.resource.data.total, repositories.resource.data.offset + repositoryItems.length), total: repositories.resource.data.total })}</small> : null}</div>
      <label className="field failure-search"><span>{t("failures.searchRepositories")}</span><input type="search" value={state.repositoryQuery} onChange={(event) => update({ repositoryQuery: event.target.value, repositoryOffset: 0 })} placeholder={t("failures.searchRepositoriesPlaceholder")} /></label>
      {repositoryItems.length === 0 && repositories.resource.status !== "read-loading" ? <p className="empty-inline">{t("failures.noRepositories")}</p> : null}
      {repositoryItems.length > 0 ? <div className="table-shell"><table><thead><tr><th>{t("failures.repository")}</th><th>{t("failures.signature")}</th><th>{t("failures.failure")}</th><th>{t("failures.run")}</th></tr></thead><tbody>{repositoryItems.map((item) => <tr key={`${item.repository.repository_id}-${item.fleet_run_id}`}><td><strong>{item.repository.name}</strong><small>{item.repository.path}</small></td><td>{item.signature_id ? <button className="text-button" type="button" onClick={() => update({ signatureId: state.signatureId === item.signature_id ? null : item.signature_id, repositoryOffset: 0 })}>{shortId(item.signature_id)}</button> : "—"}</td><td className="failure-repository-row__message">{item.failure_message ?? item.failure_code ?? "—"}</td><td className="failure-repository-row__action"><button className="button button--primary button--compact" type="button" onClick={() => navigate("runs", { run: item.fleet_run_id, focus: "failures" })}>{t("failures.openGameTape")}</button></td></tr>)}</tbody></table></div> : null}
      {state.signatureId ? <div className="button-row"><span className="muted">{t("failures.signatureFilter", { signature: shortId(state.signatureId) })}</span><button className="text-button" type="button" onClick={() => update({ signatureId: null, repositoryOffset: 0 })}>{t("failures.clearSignature")}</button></div> : null}
      <div className="button-row">
        <button className="text-button" type="button" disabled={state.repositoryOffset === 0} onClick={() => update({ repositoryOffset: Math.max(0, state.repositoryOffset - PAGE_SIZE) })}>← {t("failures.previous")}</button>
        <button className="text-button" type="button" disabled={!repositories.resource.data || state.repositoryOffset + PAGE_SIZE >= repositories.resource.data.total} onClick={() => update({ repositoryOffset: state.repositoryOffset + PAGE_SIZE })}>{t("failures.next")} →</button>
      </div>
    </section> : null}
  </main>;
}

type FailureRouteState = Readonly<{
  query: string;
  groupOffset: number;
  groupId: string | null;
  repositoryQuery: string;
  repositoryOffset: number;
  signatureId: string | null;
}>;

function readFailureRoute(): FailureRouteState {
  const params = queryParamsFromHash();
  return {
    query: params.get("q") ?? "",
    groupOffset: positiveOffset(params.get("group_offset")),
    groupId: params.get("group"),
    repositoryQuery: params.get("repository_q") ?? "",
    repositoryOffset: positiveOffset(params.get("repository_offset")),
    signatureId: params.get("signature"),
  };
}

function failureRouteQuery(state: FailureRouteState): Record<string, string> {
  const query: Record<string, string> = {};
  if (state.query.trim()) query.q = state.query;
  if (state.groupOffset > 0) query.group_offset = String(state.groupOffset);
  if (state.groupId) query.group = state.groupId;
  if (state.repositoryQuery.trim()) query.repository_q = state.repositoryQuery;
  if (state.repositoryOffset > 0) query.repository_offset = String(state.repositoryOffset);
  if (state.signatureId) query.signature = state.signatureId;
  return query;
}

function positiveOffset(value: string | null): number {
  const parsed = Number.parseInt(value ?? "0", 10);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 0;
}

function shortId(value: string): string {
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}
