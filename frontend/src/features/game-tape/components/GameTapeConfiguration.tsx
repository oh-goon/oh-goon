import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { PlanConfigurationEvidence } from "@generated/models";

const PAGE_SIZE = 50;

export function GameTapeConfiguration({ configuration }: { configuration?: PlanConfigurationEvidence | null }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const entries = configuration?.entries ?? [];
  const pageCount = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const start = currentPage * PAGE_SIZE;
  return <details className="advanced-disclosure game-tape-configuration">
    <summary>{t("gameTape.configuration.title")}</summary>
    <p className="muted">{t("gameTape.configuration.privacy")}</p>
    {!configuration ? <p>{t("gameTape.configuration.unavailable")}</p> : <>
      {configuration.caller_file_sha256 ? <p>{t("gameTape.configuration.callerFileSource")} <code>{configuration.caller_file_sha256}</code></p> : null}
      <ol start={start + 1}>{entries.slice(start, start + PAGE_SIZE).map((entry) => <li key={JSON.stringify([entry.scope, entry.namespace, entry.name])}>
        <strong><code>{entry.scope ?? t("gameTape.configuration.root")}.{entry.namespace}.{entry.name}</code></strong>
        <p>{t(`gameTape.configuration.origins.${entry.binding.kind}`)} · {t(`gameTape.configuration.states.${entry.value_state}`)}</p>
        {entry.input_type ? <p>{t("gameTape.configuration.inputType", { type: entry.input_type })}</p> : null}
        {entry.binding.parent_input ? <p>{t("gameTape.configuration.parent")} <code>{entry.binding.parent_scope ?? t("gameTape.configuration.root")}.{entry.binding.parent_input}</code></p> : null}
        {entry.binding.producer ? <p>{t("gameTape.configuration.producer")} <code>{entry.binding.producer}{entry.binding.output ? `.${entry.binding.output}` : ""}</code></p> : null}
        {entry.source_sha256 ? <p>{t("gameTape.configuration.source")} <code>{entry.source_sha256}{entry.line ? `:${entry.line}:${entry.column}` : ""}</code></p> : null}
        {entry.binding.source_sha256 ? <p>{t("gameTape.configuration.bindingSource")} <code>{entry.binding.source_sha256}{entry.binding.line ? `:${entry.binding.line}:${entry.binding.column}` : ""}</code></p> : null}
        {(entry.binding.candidates ?? []).length > 0 ? <ol aria-label={t("gameTape.configuration.candidates")}>{entry.binding.candidates?.map((candidate, index) => <li key={index}>
          <code>{candidate.kind}{candidate.name ? `:${candidate.name}` : ""}{candidate.git_field ? `:${candidate.git_field}` : ""}</code> · {t(`gameTape.configuration.candidateStates.${candidate.state}`)}
        </li>)}</ol> : null}
      </li>)}</ol>
      {entries.length === 0 ? <p>{t("gameTape.configuration.empty")}</p> : null}
      {pageCount > 1 ? <nav className="button-row" aria-label={t("gameTape.configuration.pagination")}>
        <button className="button button--secondary button--compact" type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{t("common.previous")}</button>
        <span>{t("gameTape.configuration.page", { page: currentPage + 1, total: pageCount })}</span>
        <button className="button button--secondary button--compact" type="button" disabled={currentPage + 1 === pageCount} onClick={() => setPage(currentPage + 1)}>{t("common.next")}</button>
      </nav> : null}
      {(configuration.omitted_entries ?? 0) > 0 ? <p role="status">{t("gameTape.configuration.omitted", { count: configuration.omitted_entries })}</p> : null}
    </>}
  </details>;
}
