import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { GameTape, GameTapeEvent } from "@generated/models";
import { formatTime, statusTone } from "@/features/game-tape/gameTapeFormat";

export function GameTapePlayByPlay({
  tape,
  onSelect,
}: {
  tape: GameTape;
  onSelect(id: string): void;
}) {
  const { t } = useTranslation();
  return <div className="game-tape-play-by-play">
    <details className="advanced-disclosure game-tape-ledger-contract">
      <summary>{t("gameTape.playByPlay.contractTitle")}</summary>
      <span>{t("gameTape.playByPlay.contract")}</span>
    </details>
    <ol>
      {(tape.play_by_play ?? []).map((event) => <li key={event.event_id}>
        <EventRow event={event} onSelect={onSelect} />
      </li>)}
    </ol>
  </div>;
}

function EventRow({ event, onSelect }: { event: GameTapeEvent; onSelect(id: string): void }) {
  const { t } = useTranslation();
  const body = <>
    <span className="game-tape-event__time">{formatTime(event.occurred_at)}</span>
    <span className="game-tape-event__sequence">#{event.sequence}</span>
    <span className="game-tape-event__body">
      <strong>{t(`gameTape.events.${event.event_type}`, { defaultValue: event.event_type })}</strong>
      <small>{eventContext(event)}</small>
      <span>{event.message}</span>
    </span>
    {event.status ? <StatusBadge label={t(`gameTape.status.${event.status}`)} tone={statusTone(event.status)} /> : null}
  </>;
  if (!event.step_execution_id) return <div className="game-tape-event">{body}</div>;
  return <button className="game-tape-event game-tape-event--selectable" type="button" onClick={() => onSelect(event.step_execution_id ?? "")}>{body}</button>;
}

function eventContext(event: GameTapeEvent): string {
  return [event.repository_name, event.step_id ? `${event.step_order ?? "?"}. ${event.step_id}` : null]
    .filter(Boolean)
    .join(" · ");
}
