import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  GameTape,
  GameTapeEvent,
  GameTapeLiveOutputBatch,
  GameTapeStepExecution,
} from "@generated/models";
import { command } from "@/features/game-tape/gameTapeFormat";
import { gameTapeStepContexts } from "@/features/game-tape/gameTapeSteps";

interface SearchHit {
  readonly id: string;
  readonly repository: string;
  readonly step: GameTapeStepExecution;
  readonly fields: readonly string[];
}

interface EventSearchHit {
  readonly event: GameTapeEvent;
}

export function GameTapeSearch({ tape, onSelect, liveOutput }: { tape: GameTape; onSelect(id: string): void; liveOutput: GameTapeLiveOutputBatch | null }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();
  const hits = useMemo(() => normalized ? searchSteps(tape, normalized) : [], [normalized, tape]);
  const requirementHits = useMemo(
    () => normalized ? (tape.requirements ?? []).filter((item) => `${item.kind} ${item.description}`.toLowerCase().includes(normalized)) : [],
    [normalized, tape.requirements],
  );
  const eventHits = useMemo(() => normalized ? searchEvents(tape, normalized) : [], [normalized, tape]);
  const liveHits = useMemo(() => normalized ? searchLiveOutput(tape, liveOutput, normalized) : [], [liveOutput, normalized, tape]);
  return <div className="game-tape-search">
    <label className="game-tape-search__field">
      <span>{t("gameTape.search.label")}</span>
      <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("gameTape.search.placeholder")} />
    </label>
    <p className="muted">{t("gameTape.search.scope")}</p>
    {normalized && hits.length === 0 && requirementHits.length === 0 && eventHits.length === 0 && liveHits.length === 0 ? <p>{t("gameTape.search.empty")}</p> : null}
    {liveHits.length > 0 ? <section className="game-tape-search__section"><strong>{t("gameTape.search.liveLogs")}</strong><div className="game-tape-search__hits">{liveHits.map((hit) => <button type="button" key={hit.id} onClick={() => onSelect(hit.stepExecutionId)}><span>{hit.repository}</span><strong>{hit.stepId} · {hit.stream}</strong><small>{hit.text}</small></button>)}</div></section> : null}
    {requirementHits.length > 0 ? <section className="game-tape-search__section"><strong>{t("gameTape.search.requirements")}</strong><ul>{requirementHits.map((item) => <li key={`${item.order}-${item.kind}`}><code>{item.kind}</code><span>{item.description}</span></li>)}</ul></section> : null}
    {eventHits.length > 0 ? <section className="game-tape-search__section"><strong>{t("gameTape.search.events")}</strong><div className="game-tape-search__hits">{eventHits.map(({ event }) => event.step_execution_id ? <button type="button" key={event.event_id} onClick={() => onSelect(event.step_execution_id ?? "")}><span>{event.repository_name ?? t("gameTape.title")}</span><strong>{event.event_type}</strong><small>{event.message}</small></button> : <div className="game-tape-search__fact" key={event.event_id}><span>{event.repository_name ?? t("gameTape.title")}</span><strong>{event.event_type}</strong><small>{event.message}</small></div>)}</div></section> : null}
    {hits.length > 0 ? <section className="game-tape-search__section"><strong>{t("gameTape.search.steps")}</strong><div className="game-tape-search__hits">{hits.map((hit) => <button type="button" key={hit.id} onClick={() => onSelect(hit.step.step_execution_id)}><span>{hit.repository}</span><strong>{hit.step.step_id}</strong><small>{hit.fields.join(" · ")}</small></button>)}</div></section> : null}
  </div>;
}

function searchSteps(tape: GameTape, query: string): SearchHit[] {
  const hits: SearchHit[] = [];
  for (const { repository, step } of gameTapeStepContexts(tape)) {
    const fields = matchingFields(step, query);
    if (fields.length > 0) hits.push({
      id: `${repository.repository_execution_id}:${step.step_execution_id}`,
      repository: repository.repository.name,
      step,
      fields,
    });
  }
  return hits;
}

function matchingFields(step: GameTapeStepExecution, query: string): string[] {
  const candidates: readonly [string, string][] = [
    ["step", `${step.step_id} ${step.kind} ${step.status}`],
    ["command", command(step)],
    ["provenance", (step.environment ?? []).flatMap((binding) => [binding.name, ...(binding.sources ?? [])]).join(" ")],
    ["outputs", (step.output_names ?? []).join(" ")],
    ["why", `${step.why.declaration} ${step.why.condition ?? ""} ${step.why.explanation}`],
    ["failure", `${step.failure?.exit_code ?? ""} ${step.failure?.reason_code ?? ""} ${step.failure?.signature_id ?? ""}`],
    ["attempts", (step.attempts ?? []).map((attempt) => [
      attempt.attempt,
      ...(attempt.argv ?? []),
      attempt.returncode,
      attempt.tool?.name ?? "",
      attempt.tool?.origin ?? "",
      attempt.egress.mode,
      attempt.retry?.disposition ?? "",
    ].join(" ")).join(" ")],
  ];
  return candidates.filter(([, value]) => value.toLowerCase().includes(query)).map(([label]) => label);
}

function searchEvents(tape: GameTape, query: string): EventSearchHit[] {
  return (tape.play_by_play ?? [])
    .filter((event) => `${event.event_type} ${event.repository_name ?? ""} ${event.step_id ?? ""} ${event.message}`.toLowerCase().includes(query))
    .map((event) => ({ event }));
}

interface LiveSearchHit {
  readonly id: string;
  readonly repository: string;
  readonly stepId: string;
  readonly stepExecutionId: string;
  readonly stream: string;
  readonly text: string;
}

function searchLiveOutput(
  tape: GameTape,
  liveOutput: GameTapeLiveOutputBatch | null,
  query: string,
): LiveSearchHit[] {
  if (!liveOutput?.available) return [];
  const steps = new Map<string, { repository: string; executionId: string }>();
  for (const { repository, step } of gameTapeStepContexts(tape)) {
    steps.set(`${repository.repository.repository_id}:${step.step_id}`, {
      repository: repository.repository.name,
      executionId: step.step_execution_id,
    });
  }
  return (liveOutput.chunks ?? []).flatMap((chunk) => {
    if (!chunk.text.toLowerCase().includes(query)) return [];
    const step = steps.get(`${chunk.repository_id}:${chunk.step_id}`);
    if (!step) return [];
    return [{
      id: `live:${chunk.sequence}`,
      repository: step.repository,
      stepId: chunk.step_id,
      stepExecutionId: step.executionId,
      stream: chunk.stream,
      text: chunk.text.trim().slice(0, 180),
    }];
  });
}
