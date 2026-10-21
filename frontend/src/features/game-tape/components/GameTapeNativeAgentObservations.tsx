import { useTranslation } from "react-i18next";

import type { NativeAgentObservation } from "@generated/models";

export function GameTapeNativeAgentObservations({ observations }: { observations: readonly NativeAgentObservation[] }) {
  const { t } = useTranslation();
  if (observations.length === 0) return null;
  return <section className="game-tape-provenance" aria-label={t("gameTape.nativeAgent.title")}>
    <strong>{t("gameTape.nativeAgent.title")}</strong>
    <p>{t("gameTape.nativeAgent.claimRule")}</p>
    {observations.map((observation, index) => <details key={`${observation.input_identity}:${index}`}>
      <summary>{observation.agent} · {observation.profile} · {observation.role}</summary>
      <dl className="game-tape-inspector__facts">
        <div><dt>{t("gameTape.nativeAgent.implementation")}</dt><dd><code>{observation.implementation_identity}</code></dd></div>
        <div><dt>{t("gameTape.nativeAgent.input")}</dt><dd><code>{observation.input_identity}</code></dd></div>
        <div><dt>{t("gameTape.nativeAgent.source")}</dt><dd><code>{observation.source_identity}</code></dd></div>
        <div><dt>{t("gameTape.nativeAgent.occurrence")}</dt><dd><code>{observation.occurrence}</code></dd></div>
        <div><dt>{t("gameTape.nativeAgent.settled")}</dt><dd>{t(observation.settled ? "common.yes" : "common.no")}</dd></div>
        <div><dt>{t("gameTape.nativeAgent.failed")}</dt><dd>{t(observation.failed ? "common.yes" : "common.no")}</dd></div>
        <div><dt>{t("gameTape.nativeAgent.events")}</dt><dd>{observation.native_event_count}</dd></div>
        <div><dt>{t("gameTape.nativeAgent.omitted")}</dt><dd>{observation.omitted_observations ?? 0}</dd></div>
      </dl>
      <ul>{(observation.events ?? []).map((event, eventIndex) => <li key={eventIndex}><span>{event.kind}</span><span>{event.summary}</span>{event.native_event_id ? <code>{event.native_event_id}</code> : null}</li>)}</ul>
    </details>)}
  </section>;
}
