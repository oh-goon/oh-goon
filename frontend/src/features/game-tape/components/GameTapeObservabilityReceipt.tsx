import { useTranslation } from "react-i18next";

import type { GameTape } from "@generated/models";

export function GameTapeObservabilityReceipt({ tape }: { tape: GameTape }) {
  const { t } = useTranslation();
  const receipt = tape.observability;
  return <section className="game-tape-section game-tape-observability-receipt">
    <header><div><span className="eyebrow">{t("gameTape.observability.eyebrow")}</span><h3>{t("gameTape.observability.title")}</h3></div></header>
    <div className="game-tape-observability-receipt__classes">
      <article>
        <strong>{t("gameTape.observability.durable")}</strong>
        <ul>{receipt.durable_evidence.map((item) => <li key={item}>{t(`gameTape.observability.evidence.${item}`)}</li>)}</ul>
      </article>
      <article>
        <strong>{t("gameTape.observability.transient")}</strong>
        <ul>{receipt.transient_evidence.map((item) => <li key={item}>{t(`gameTape.observability.evidence.${item}`)}</li>)}</ul>
      </article>
    </div>
    <details className="advanced-disclosure game-tape-observability-receipt__details">
      <summary>{t("gameTape.observability.technicalDetails")}</summary>
      <dl className="game-tape-observability-receipt__facts">
        <div><dt>{t("gameTape.observability.maxChunks")}</dt><dd>{receipt.raw_output_max_chunks}</dd></div>
        <div><dt>{t("gameTape.observability.maxChunkChars")}</dt><dd>{receipt.raw_output_max_chunk_chars}</dd></div>
        <div><dt>{t("gameTape.observability.handoff")}</dt><dd>{t("gameTape.observability.seconds", { count: receipt.terminal_handoff_seconds })}</dd></div>
        <div><dt>{t("gameTape.observability.restart")}</dt><dd>{receipt.raw_output_survives_process_restart ? t("common.yes") : t("common.no")}</dd></div>
        <div><dt>{t("gameTape.observability.rawStore")}</dt><dd>{receipt.persistent_raw_output_store ? t("common.yes") : t("common.no")}</dd></div>
        <div><dt>{t("gameTape.observability.searchIndex")}</dt><dd>{receipt.persistent_search_index ? t("common.yes") : t("common.no")}</dd></div>
        <div><dt>{t("gameTape.observability.executionAuthority")}</dt><dd>{receipt.ui_execution_authority ? t("common.yes") : t("common.no")}</dd></div>
        <div><dt>{t("gameTape.observability.orderAuthority")}</dt><dd>{t(`gameTape.observability.authority.${receipt.execution_order_authority}`)}</dd></div>
        <div><dt>{t("gameTape.observability.failureNavigation")}</dt><dd>{t(`gameTape.observability.failureSource.${receipt.failure_navigation_source}`)}</dd></div>
      </dl>
    </details>
    <small>{t("gameTape.observability.contract")}</small>
  </section>;
}
