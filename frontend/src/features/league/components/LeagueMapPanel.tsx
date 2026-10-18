import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { LeagueMap, LeagueTeam } from "@generated/models";

interface Props {
  league: LeagueMap | null;
  loading?: boolean;
  onRefresh?: () => void;
  onRunTeam?: (repository: string, expectedPipelineSha256: string, expectedRepositoryInstanceId: string) => void;
  actionsDisabled?: boolean;
  activeRepositoryId?: string | null;
  advanced?: boolean;
  showSummary?: boolean;
}

const TEAM_PAGE_SIZE = 60;

type BadgeTone = "success" | "warning" | "danger";

function pipelineTone(team: LeagueTeam): BadgeTone {
  if (team.pipeline.state === "CONFIGURED") return "success";
  if (team.pipeline.state === "INVALID") return "danger";
  return "warning";
}

export function LeagueMapPanel({ league, loading = false, onRefresh, onRunTeam, actionsDisabled = false, activeRepositoryId = null, advanced = false, showSummary = true }: Props) {
  const { t } = useTranslation();
  const teams = league?.teams ?? [];
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const normalizedQuery = query.trim().toLowerCase();
  const visibleTeams = normalizedQuery
    ? teams.filter((team) => `${team.repository.name} ${team.repository.path}`.toLowerCase().includes(normalizedQuery))
    : teams;
  const pageCount = Math.max(1, Math.ceil(visibleTeams.length / TEAM_PAGE_SIZE));
  const boundedPage = Math.min(page, pageCount - 1);
  useEffect(() => { if (page !== boundedPage) setPage(boundedPage); }, [boundedPage, page]);
  const pageStart = boundedPage * TEAM_PAGE_SIZE;
  const pageTeams = visibleTeams.slice(pageStart, pageStart + TEAM_PAGE_SIZE);
  useEffect(() => setPage(0), [query]);
  const nameCounts = new Map<string, number>();
  for (const team of teams) nameCounts.set(team.repository.name, (nameCounts.get(team.repository.name) ?? 0) + 1);
  return <section className="panel league-map-panel">
    <div className="section-heading">
      <div>
        <span className="eyebrow">{t("league.eyebrow")}</span>
        <h2>{t("league.title")}</h2>
        <p>{t("league.description")}</p>
      </div>
      {onRefresh ? <button className="button button--secondary" type="button" onClick={onRefresh}>{t("league.scan")}</button> : null}
    </div>
    {league && showSummary ? <div className="league-scoreboard" role="group" aria-label={t("league.summary.ariaLabel")}>
      <LeagueCount label={t("league.summary.teams")} value={league.summary.git_repositories} />
      <LeagueCount label={t("league.summary.ready")} value={league.summary.configured_teams} tone="ready" />
      <LeagueCount label={t("league.summary.missing")} value={league.summary.missing_goon_yml} />
      <LeagueCount label={t("league.summary.invalid")} value={league.summary.invalid_goon_yml} tone={league.summary.invalid_goon_yml > 0 ? "attention" : undefined} />
    </div> : null}
    {loading ? <div className="league-loading" role="status"><span className="loading-dot" aria-hidden="true" />{t("league.loading")}</div> : null}
    {teams.length > 6 ? <div className="league-team-search"><label><span>{t("league.search")}</span><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("league.searchPlaceholder")} /></label><span className="muted" role="status">{t("league.visibleCount", { visible: visibleTeams.length, total: teams.length })}</span></div> : null}
    {!loading && league && teams.length === 0 ? <div className="notice notice--scope"><strong>{t("league.emptyTitle")}</strong><p>{t("league.emptyMessage")}</p></div> : null}
    {teams.length > 0 && visibleTeams.length === 0 ? <div className="empty-inline"><span>{t("league.noMatches")}</span><button className="text-button" type="button" onClick={() => setQuery("")}>{t("common.clearFilters")}</button></div> : null}
    {visibleTeams.length > 0 && !advanced ? <div className="league-team-list" role="list">
      {pageTeams.map((team) => <article className="league-team-row" role="listitem" key={team.repository.repository_id}>
        <div className="league-team-row__identity">
          <strong>{team.repository.name}</strong>
          {(nameCounts.get(team.repository.name) ?? 0) > 1 ? <small>{team.repository.path}</small> : null}
        </div>
        <StatusBadge label={t(`league.pipeline.${team.pipeline.state}`)} tone={pipelineTone(team)} />
        <div className="league-team-row__action">
          {onRunTeam && team.pipeline.state === "CONFIGURED" ? <button className="button button--secondary button--compact" type="button" disabled={actionsDisabled} onClick={() => onRunTeam(team.repository.path, configuredPipelineSha(team), team.repository_instance_id)}>{team.repository.repository_id === activeRepositoryId ? t("league.onIce") : t("league.runTeam")}</button> : <span className="league-team-card__blocked">{t("league.notRunnable")}</span>}
        </div>
      </article>)}
    </div> : null}
    {visibleTeams.length > 0 && advanced ? <div className="table-shell"><table>
      <thead><tr><th>{t("league.columns.team")}</th><th>{t("league.columns.status")}</th><th>{t("league.columns.pipeline")}</th><th>{t("league.columns.identity")}</th>{onRunTeam ? <th>{t("league.columns.action")}</th> : null}</tr></thead>
      <tbody>{pageTeams.map((team) => <tr key={team.repository.repository_id}>
        <td data-label={t("league.columns.team")}><strong>{team.repository.name}</strong><small>{team.repository.path}</small></td>
        <td data-label={t("league.columns.status")}><StatusBadge label={t(`league.pipeline.${team.pipeline.state}`)} tone={pipelineTone(team)} /></td>
        <td data-label={t("league.columns.pipeline")}><code>{team.pipeline.path}</code></td>
        <td data-label={t("league.columns.identity")}>{team.pipeline.state === "CONFIGURED" ? <code title={team.pipeline.sha256}>{team.pipeline.sha256.slice(0, 12)}…</code> : team.pipeline.state === "INVALID" ? t(`league.invalid.${team.pipeline.reason}`) : t("league.notRunnable")}</td>
        {onRunTeam ? <td data-label={t("league.columns.action")}>{team.pipeline.state === "CONFIGURED" ? <button className="button button--primary button--compact" type="button" disabled={actionsDisabled} onClick={() => onRunTeam(team.repository.path, configuredPipelineSha(team), team.repository_instance_id)}>{team.repository.repository_id === activeRepositoryId ? t("league.onIce") : t("league.runTeam")}</button> : <span className="muted">{t("league.notRunnable")}</span>}</td> : null}
      </tr>)}</tbody>
    </table></div> : null}
    {visibleTeams.length > TEAM_PAGE_SIZE ? <nav className="league-pagination" aria-label={t("league.paginationLabel")}><button className="button button--secondary" type="button" disabled={boundedPage === 0} onClick={() => setPage(Math.max(0, boundedPage - 1))}>{t("common.previous")}</button><span>{t("league.pageRange", { from: pageStart + 1, to: Math.min(pageStart + pageTeams.length, visibleTeams.length), total: visibleTeams.length })}</span><button className="button button--secondary" type="button" disabled={boundedPage + 1 >= pageCount} onClick={() => setPage(Math.min(pageCount - 1, boundedPage + 1))}>{t("common.next")}</button></nav> : null}
  </section>;
}

function LeagueCount({ label, value, tone }: { label: string; value: number; tone?: "ready" | "attention" }) {
  return <div className={tone ? `league-score league-score--${tone}` : "league-score"}><strong>{value}</strong><span>{label}</span></div>;
}


function configuredPipelineSha(team: LeagueTeam): string {
  if (team.pipeline.state !== "CONFIGURED") throw new Error(`Team ${team.repository.repository_id} is not runnable`);
  return team.pipeline.sha256;
}
