import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { GameTape } from "@generated/models";
import { duration, formatDateTime, statusTone } from "@/features/game-tape/gameTapeFormat";

const OVERLAP_NAME_LIMIT = 20;

export function GameTapeTeamOverview({ tape, compact = false, onSelect }: { tape: GameTape; compact?: boolean; onSelect?(id: string): void }) {
  const { t } = useTranslation();
  const team = tape.team;
  if (team.repository_count <= 1) return null;
  const overlapNames = new Map(
    tape.repositories.map((repository) => [repository.repository_execution_id, repository.repository.name]),
  );
  const overlappingRepositoryExecutionIds = team.overlapping_repository_execution_ids ?? [];
  return <section className={compact ? "game-tape-team game-tape-team--compact" : "game-tape-section game-tape-team"}>
    <header>
      <div><span className="eyebrow">{t("gameTape.team.eyebrow")}</span><h3>{t("gameTape.team.title")}</h3></div>
      <span>{t("gameTape.team.repositories", { count: team.repository_count })}</span>
    </header>
    <dl className="game-tape-team__facts">
      <div><dt>{t("gameTape.team.span")}</dt><dd>{duration(team.duration_ms)}</dd></div>
      <div><dt>{t("gameTape.team.timed")}</dt><dd>{team.timed_repository_count} / {team.repository_count}</dd></div>
      <div><dt>{t("gameTape.team.peak")}</dt><dd>{team.observed_peak_concurrency}</dd></div>
      <div><dt>{t("gameTape.team.overlap")}</dt><dd>{team.overlap_observed ? t("common.yes") : t("common.no")}</dd></div>
    </dl>
    <div className="game-tape-team__statuses">
      {team.status_counts && team.status_counts.map((item) => <span key={item.status}>
        <StatusBadge label={t(`gameTape.status.${item.status}`)} tone={statusTone(item.status)} />
        <b>{item.count}</b>
      </span>)}
    </div>
    {!compact && overlappingRepositoryExecutionIds.length > 0 ? <p>
      <strong>{t("gameTape.team.overlapping")}</strong>{" "}
      {overlappingRepositoryExecutionIds.slice(0, OVERLAP_NAME_LIMIT).map((id) => overlapNames.get(id) ?? id).join(", ")}
      {overlappingRepositoryExecutionIds.length > OVERLAP_NAME_LIMIT ? ` ${t("gameTape.team.moreOverlapping", { count: overlappingRepositoryExecutionIds.length - OVERLAP_NAME_LIMIT })}` : ""}
    </p> : null}
    {team.first_failure ? <div className="game-tape-team__failure">
      <strong>{t("gameTape.team.firstFailure")}</strong>
      {onSelect && team.first_failure.step_execution_id ? <button type="button" onClick={() => onSelect(team.first_failure?.step_execution_id ?? "")}>
        {team.first_failure.repository_name}{team.first_failure.step_id ? ` · ${team.first_failure.step_id}` : ""}
      </button> : <span>{team.first_failure.repository_name}{team.first_failure.step_id ? ` · ${team.first_failure.step_id}` : ""}</span>}
      {team.first_failure.occurred_at ? <small>{formatDateTime(team.first_failure.occurred_at)}</small> : null}
    </div> : null}
    <small>{t("gameTape.team.contract")}</small>
  </section>;
}
