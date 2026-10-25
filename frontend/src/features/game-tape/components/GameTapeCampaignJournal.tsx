import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { AgentCampaignReadback } from "@generated/models";

const PAGE_SIZE = 30;

export function GameTapeCampaignJournal({ campaign }: { campaign?: AgentCampaignReadback | null }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  if (!campaign) return null;
  const latest = campaign.records.at(-1)?.fact;
  const terminal = latest?.kind === "decision" && latest.stop_reason ? latest : null;
  const pageCount = Math.max(1, Math.ceil(campaign.records.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const start = currentPage * PAGE_SIZE;
  return <section className="game-tape-section" aria-label={t("gameTape.campaign.title")}>
    <h3>{t("gameTape.campaign.title")}</h3>
    <dl className="game-tape-receipt">
      <div><dt>{t("gameTape.campaign.identity")}</dt><dd><code>{campaign.campaign_id}</code></dd></div>
      <div><dt>{t("gameTape.campaign.readCut")}</dt><dd>{campaign.journal_revision}</dd></div>
      <div><dt>{t("gameTape.campaign.decision")}</dt><dd>{terminal && campaign.complete ? terminal.stop_reason : t("gameTape.campaign.missingDecision")}</dd></div>
    </dl>
    {!campaign.complete ? <p role="status">{t("gameTape.campaign.incomplete")}</p> : null}
    {!terminal ? <p>{t("gameTape.campaign.noInference")}</p> : null}
    {terminal?.message ? <p>{terminal.message}</p> : null}
    <details className="advanced-disclosure">
      <summary>{t("gameTape.campaign.records")}</summary>
      <ol start={start + 1}>{campaign.records.slice(start, start + PAGE_SIZE).map((record) => {
        const fact = record.fact;
        return <li key={record.revision}>
          <strong>{t("gameTape.campaign.revision", { revision: record.revision })} · {fact.kind}</strong>
          {fact.kind === "intent" ? <dl className="game-tape-receipt">
            <div><dt>{t("gameTape.campaign.repository")}</dt><dd><code>{fact.repository_id}</code></dd></div>
            <div><dt>{t("gameTape.campaign.execution")}</dt><dd><code>{fact.original_execution_digest}</code></dd></div>
            <div><dt>{t("gameTape.campaign.initialPhase")}</dt><dd>{fact.initial_phase}</dd></div>
            <CampaignStepMembership label={t("gameTape.campaign.initialSteps")} ids={fact.initial_step_ids} />
            <div><dt>{t("gameTape.campaign.maximumRuns")}</dt><dd>{fact.maximum_runs}</dd></div>
            <div><dt>{t("gameTape.campaign.deadline")}</dt><dd>{fact.deadline_at}</dd></div>
            <div><dt>{t("gameTape.campaign.candidate")}</dt><dd><code>{fact.candidate_identity ?? "—"}</code></dd></div>
            <div><dt>{t("gameTape.campaign.scope")}</dt><dd><code>{fact.scope_identity ?? "—"}</code></dd></div>
          </dl> : fact.kind === "phase" ? <dl className="game-tape-receipt">
            <div><dt>{t("gameTape.campaign.phase")}</dt><dd>{fact.context.phase ?? "—"} · {fact.context.attempt}</dd></div>
            <div><dt>{t("gameTape.campaign.run")}</dt><dd><code>{fact.fleet_run_id}</code></dd></div>
            <CampaignStepMembership label={t("gameTape.campaign.steps")} ids={fact.selected_step_ids} />
            <div><dt>{t("gameTape.campaign.execution")}</dt><dd><code>{fact.execution_digest}</code></dd></div>
          </dl> : fact.kind === "decision" ? <>
            <dl className="game-tape-receipt">
              <div><dt>{t("gameTape.campaign.decisionIdentity")}</dt><dd><code>{fact.decision_id}</code></dd></div>
              <div><dt>{t("gameTape.campaign.decision")}</dt><dd>{fact.stop_reason ?? fact.next_phase ?? "—"}</dd></div>
              {fact.next_phase ? <div><dt>{t("gameTape.campaign.nextPhase")}</dt><dd>{fact.next_phase}</dd></div> : null}
              {fact.next_phase || (fact.next_step_ids?.length ?? 0) > 0 ? <CampaignStepMembership label={t("gameTape.campaign.nextSteps")} ids={fact.next_step_ids ?? []} /> : null}
              <div><dt>{t("gameTape.campaign.remainingRuns")}</dt><dd>{fact.remaining_runs}</dd></div>
              <div><dt>{t("gameTape.campaign.unresolved")}</dt><dd>{fact.unresolved_run_ids?.join(", ") || "—"}</dd></div>
            </dl>
            {fact.unstarted_selection ? <section className="game-tape-provenance" aria-label={t("gameTape.campaign.unstarted")}>
              <strong>{t("gameTape.campaign.unstarted")}</strong>
              <p>{t("gameTape.campaign.unstartedRule")}</p>
              <dl className="game-tape-receipt">
                <div><dt>{t("gameTape.campaign.selectedRun")}</dt><dd><code>{fact.unstarted_selection.selected_run_id}</code></dd></div>
                <div><dt>{t("gameTape.campaign.admissionStatus")}</dt><dd>{fact.unstarted_selection.admission_status ?? "—"}</dd></div>
                <div><dt>{t("gameTape.campaign.phase")}</dt><dd>{fact.unstarted_selection.phase} · {fact.unstarted_selection.phase_ordinal}</dd></div>
                <CampaignStepMembership label={t("gameTape.campaign.steps")} ids={fact.unstarted_selection.selected_step_ids} />
              </dl>
            </section> : null}
            {fact.message ? <p>{fact.message}</p> : null}
            <ul>{fact.consumed_runs?.map((run) => <li key={run.fleet_run_id}><code>{run.fleet_run_id}</code> · {t("gameTape.campaign.terminalSequence", { sequence: run.terminal_sequence })}</li>)}</ul>
          </> : <p>{t("gameTape.campaign.unavailableRecord")}</p>}
        </li>;
      })}</ol>
      {pageCount > 1 ? <nav className="button-row" aria-label={t("gameTape.campaign.pagination")}>
        <button className="button button--secondary button--compact" type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{t("common.previous")}</button>
        <span>{t("gameTape.campaign.page", { page: currentPage + 1, total: pageCount })}</span>
        <button className="button button--secondary button--compact" type="button" disabled={currentPage + 1 === pageCount} onClick={() => setPage(currentPage + 1)}>{t("common.next")}</button>
      </nav> : null}
    </details>
  </section>;
}

function CampaignStepMembership({ label, ids }: { label: string; ids: readonly string[] }) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(ids.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const start = currentPage * PAGE_SIZE;
  return <div>
    <dt>{label}</dt>
    <dd>
      <span>{t("gameTape.campaign.membershipCount", { count: ids.length })}</span>
      <ol start={start + 1}>{ids.slice(start, start + PAGE_SIZE).map((id, index) => <li key={`${start + index}:${id}`}><code>{id}</code></li>)}</ol>
      {pageCount > 1 ? <nav className="button-row" aria-label={t("gameTape.campaign.membershipPagination", { label })}>
        <button className="button button--secondary button--compact" type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{t("common.previous")}</button>
        <span>{t("gameTape.campaign.page", { page: currentPage + 1, total: pageCount })}</span>
        <button className="button button--secondary button--compact" type="button" disabled={currentPage + 1 === pageCount} onClick={() => setPage(currentPage + 1)}>{t("common.next")}</button>
      </nav> : null}
    </dd>
  </div>;
}
