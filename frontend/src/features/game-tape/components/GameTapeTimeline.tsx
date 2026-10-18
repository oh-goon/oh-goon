import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { GameTape } from "@generated/models";
import { StatusBadge } from "@/components/common/StatusBadge";
import { GameTapeTeamOverview } from "@/features/game-tape/components/GameTapeTeamOverview";
import { duration, formatTime, statusSymbol, statusTone } from "@/features/game-tape/gameTapeFormat";
import { gameTapeTimelineInterval, gameTapeTimelinePosition, gameTapeTimelineScale } from "@/features/game-tape/gameTapeTimelineModel";

const GAME_TAPE_TIMELINE_REPOSITORY_PAGE_SIZE = 40;

export function GameTapeTimeline({ tape, selectedId, onSelect, failureFocus = false }: { tape: GameTape; selectedId: string | null; onSelect(id: string): void; failureFocus?: boolean }) {
  const { t } = useTranslation();
  const allSteps = tape.repositories.flatMap((repository) => repository.steps ?? []);
  const scale = gameTapeTimelineScale(allSteps, { startedAt: tape.receipt.started_at, finishedAt: tape.receipt.finished_at, durationMs: tape.receipt.duration_ms });
  const statuses = Array.from(new Set(allSteps.map((step) => step.status)));
  const selectedStep = allSteps.find((step) => step.step_execution_id === selectedId) ?? null;
  const selectedInterval = selectedStep ? gameTapeTimelineInterval(selectedStep) : null;
  const selectedPosition = selectedInterval && scale ? gameTapeTimelinePosition(selectedInterval, scale) : null;
  const [repositoryPage, setRepositoryPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(tape.repositories.length / GAME_TAPE_TIMELINE_REPOSITORY_PAGE_SIZE));
  const boundedPage = Math.min(repositoryPage, pageCount - 1);
  useEffect(() => { if (repositoryPage !== boundedPage) setRepositoryPage(boundedPage); }, [boundedPage, repositoryPage]);
  const pageStart = boundedPage * GAME_TAPE_TIMELINE_REPOSITORY_PAGE_SIZE;
  const pageRepositories = tape.repositories.slice(pageStart, pageStart + GAME_TAPE_TIMELINE_REPOSITORY_PAGE_SIZE);
  const selectedRepositoryIndex = selectedId === null ? -1 : tape.repositories.findIndex((repository) => (repository.steps ?? []).some((step) => step.step_execution_id === selectedId));
  useEffect(() => { if (selectedRepositoryIndex >= 0) setRepositoryPage(Math.floor(selectedRepositoryIndex / GAME_TAPE_TIMELINE_REPOSITORY_PAGE_SIZE)); }, [selectedRepositoryIndex]);
  return <div className="game-tape-timeline" data-failure-focus={failureFocus ? "true" : undefined}>
    <GameTapeTeamOverview tape={tape} compact onSelect={onSelect} />
    <section className="compact-card" aria-label={t("gameTape.timeline.runTimingSummary")}><div><span className="eyebrow">{t("gameTape.timeline.runTimingSummary")}</span><strong>{t("gameTape.timeline.runTimingRange", { start: formatTime(tape.receipt.started_at), end: tape.receipt.finished_at ? formatTime(tape.receipt.finished_at) : t("gameTape.timeline.open") })}</strong><small>{t("gameTape.timeline.runRecordedDuration", { duration: duration(tape.receipt.duration_ms) })}</small>{scale ? <small>{t("gameTape.timeline.fixedScale", { duration: duration(scale.spanMs) })}</small> : null}</div><div className="button-row" role="group" aria-label={t("gameTape.timeline.statusLegend")}>{statuses.map((status) => <span className="game-tape-timeline-step" key={status}><b className="game-tape-status-mark" data-status={status} aria-hidden="true">{statusSymbol(status)}</b><span><strong>{t(`gameTape.status.${status}`)}</strong></span></span>)}</div></section>
    {selectedStep ? <aside className="game-tape-timeline-focus" data-status={selectedStep.status} aria-label={t("gameTape.timeline.selectedTiming")}><span className="game-tape-status-mark" data-status={selectedStep.status} aria-hidden="true">{statusSymbol(selectedStep.status)}</span><div><span className="eyebrow">{t("gameTape.timeline.selectedTiming")}</span><strong>{selectedStep.step_id}</strong><small>{t(`gameTape.status.${selectedStep.status}`)}</small></div><dl><div><dt>{t("gameTape.timeline.selectedStart")}</dt><dd>{formatTime(selectedStep.started_at)}</dd></div><div><dt>{selectedStep.finished_at ? t("gameTape.timeline.selectedEnd") : t("gameTape.timeline.selectedRecordedThrough")}</dt><dd>{selectedInterval ? formatTime(new Date(selectedInterval.endMs).toISOString()) : "—"}</dd></div><div><dt>{t("gameTape.timeline.selectedDuration")}</dt><dd>{duration(selectedStep.duration_ms)}</dd></div></dl></aside> : null}
    {scale === null ? <p className="muted">{t("gameTape.timeline.unavailable")}</p> : <TimelineAxis minMs={scale.minMs} maxMs={scale.maxMs} spanMs={scale.spanMs} ticks={scale.ticks} selectedPercent={selectedPosition?.leftPercent ?? null} selectedStatus={selectedStep?.status ?? null} />}
    {pageRepositories.map((repository) => <section key={repository.repository_execution_id}>
      <header className="game-tape-timeline__lane-heading" data-status={repository.status}>
        <div className="game-tape-timeline__lane-identity"><b className="game-tape-status-mark" data-status={repository.status} aria-hidden="true">{statusSymbol(repository.status)}</b><div><strong>{repository.repository.name}</strong><small>{formatTime(repository.started_at)} → {formatTime(repository.finished_at)}</small></div></div>
        <div className="game-tape-timeline__lane-meta"><StatusBadge label={t(`gameTape.status.${repository.status}`)} tone={statusTone(repository.status)} /><small>{t("gameTape.timeline.recordedLaneDuration", { duration: duration(repository.duration_ms) })}</small></div>
      </header>
      {repository.bottleneck_step_id ? <p className="game-tape-bottleneck"><strong>{t("gameTape.timeline.bottleneck")}</strong> {t("gameTape.timeline.bottleneckValue", { step: repository.bottleneck_step_id, percent: repository.bottleneck_percent ?? 0 })}</p> : null}
      <div className="game-tape-timeline__rows">
        {(repository.steps ?? []).map((step) => {
          const interval = gameTapeTimelineInterval(step);
          const position = interval && scale ? gameTapeTimelinePosition(interval, scale) : null;
          return <button
            className={selectedId === step.step_execution_id ? "game-tape-timeline-row game-tape-timeline-row--selected" : "game-tape-timeline-row"}
            data-status={step.status}
            type="button"
            key={step.step_execution_id}
            aria-label={t("gameTape.timeline.stepAria", { step: step.step_id, status: t(`gameTape.status.${step.status}`), duration: duration(step.duration_ms) })}
            onClick={() => onSelect(step.step_execution_id)}
          >
            <span className="game-tape-timeline-step"><b className="game-tape-status-mark" data-status={step.status} aria-hidden="true">{statusSymbol(step.status)}</b><span><strong>{step.step_id}</strong><small>{t(`gameTape.status.${step.status}`)}</small></span></span>
            <span className="game-tape-timeline-track" aria-hidden="true">
              {selectedPosition ? <b className="game-tape-timeline-cursor" data-status={selectedStep?.status ?? undefined} style={{ left: `${selectedPosition.leftPercent}%` }} /> : null}
              {position ? <i style={{ left: `${position.leftPercent}%`, width: `${position.widthPercent}%` }} data-status={step.status}><b className="game-tape-status-mark game-tape-timeline-endmark" data-status={step.status}>{statusSymbol(step.status)}</b></i> : <em>{t(`gameTape.status.${step.status}`)}</em>}
            </span>
            <small>{duration(step.duration_ms)}</small>
          </button>;
        })}
      </div>
    </section>)}
    {tape.repositories.length > GAME_TAPE_TIMELINE_REPOSITORY_PAGE_SIZE ? <nav className="game-tape-repository-pagination" aria-label={t("gameTape.repositoryPaginationLabel")}><button className="button button--secondary button--compact" type="button" disabled={boundedPage === 0} onClick={() => setRepositoryPage(Math.max(0, boundedPage - 1))}>{t("common.previous")}</button><span>{t("gameTape.repositoryPageRange", { from: pageStart + 1, to: Math.min(pageStart + pageRepositories.length, tape.repositories.length), total: tape.repositories.length })}</span><button className="button button--secondary button--compact" type="button" disabled={boundedPage + 1 >= pageCount} onClick={() => setRepositoryPage(Math.min(pageCount - 1, boundedPage + 1))}>{t("common.next")}</button></nav> : null}
    <p className="game-tape-timeline__truth">{t("gameTape.timeline.truth")}</p>
  </div>;
}

function TimelineAxis({ minMs, maxMs, spanMs, ticks, selectedPercent, selectedStatus }: { minMs: number; maxMs: number; spanMs: number; ticks: readonly number[]; selectedPercent: number | null; selectedStatus: string | null }) {
  const { t } = useTranslation();
  return <div className="game-tape-timeline-axis" role="group" aria-label={t("gameTape.timeline.axisLabel")}>
    <span>{t("gameTape.timeline.stepColumn")}</span>
    <div className="game-tape-timeline-axis__ruler">{selectedPercent !== null ? <b className="game-tape-timeline-cursor" data-status={selectedStatus ?? undefined} style={{ left: `${selectedPercent}%` }} aria-hidden="true" /> : null}
      {ticks.map((tick, index) => {
        const left = ((tick - minMs) / spanMs) * 100;
        const label = index === 0 ? t("gameTape.timeline.axisStart", { time: formatTime(new Date(minMs).toISOString()) }) : index === ticks.length - 1 ? t("gameTape.timeline.axisScaleEnd", { duration: duration(maxMs - minMs) }) : t("gameTape.timeline.axisElapsed", { duration: duration(tick - minMs) });
        return <span className="game-tape-timeline-axis__tick" data-edge={index === 0 ? "start" : index === ticks.length - 1 ? "end" : undefined} key={tick} style={{ left: `${left}%` }}><i aria-hidden="true" /><small>{label}</small></span>;
      })}
    </div>
    <span>{t("gameTape.timeline.durationColumn")}</span>
  </div>;
}
