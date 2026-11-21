import { useTranslation } from "react-i18next";

import type { RunFeedback } from "@generated/models";

export function GameTapeRunFeedback({ feedback, runId, campaignId, journalRevision, status, outcome }: { feedback?: RunFeedback | null; runId?: string; campaignId?: string; journalRevision?: number | null; status?: string; outcome?: string }) {
  const { t } = useTranslation();
  if (!feedback) return null;
  if (runId !== undefined && feedback.fleet_run_id !== null && feedback.fleet_run_id !== runId
    || campaignId !== undefined && feedback.campaign?.campaign_id != null && feedback.campaign.campaign_id !== campaignId
    || journalRevision !== undefined && journalRevision !== null && feedback.journal_revision !== journalRevision
    || status !== undefined && feedback.status !== status
    || outcome !== undefined && feedback.outcome !== outcome
    || feedback.fleet_run_id !== null && feedback.controls.fleet_run_id != null && feedback.controls.fleet_run_id !== feedback.fleet_run_id
    || feedback.controls.journal_revision != null && feedback.controls.journal_revision !== feedback.journal_revision) {
    return <p role="alert">{t("gameTape.feedback.identityMismatch")}</p>;
  }
  return <section className="game-tape-section game-tape-run-feedback" aria-label={t("gameTape.feedback.title")}>
    <h3>{t("gameTape.feedback.title")}</h3>
    <p>{t("gameTape.feedback.authority")}</p>
    {feedback.continuation_state ? <p role="status">{t(`campaignContinuation.states.${feedback.continuation_state}`)}</p> : null}
    <strong>{feedback.diagnostic.classification}</strong>
    <p>{feedback.diagnostic.reason}</p>
    <dl className="game-tape-receipt">
      <div><dt>{t("gameTape.campaign.run")}</dt><dd><code>{feedback.fleet_run_id ?? t("gameTape.feedback.unavailable")}</code></dd></div>
      <div><dt>{t("gameTape.campaign.readCut")}</dt><dd>{feedback.journal_revision ?? t("gameTape.feedback.unavailable")}</dd></div>
      <div><dt>{t("gameTape.feedback.recordedOutcome")}</dt><dd>{feedback.status} · {feedback.outcome}</dd></div>
      {feedback.campaign ? <>
        <div><dt>{t("gameTape.feedback.runCandidate")}</dt><dd><code>{feedback.campaign.run_candidate_identity ?? t("gameTape.feedback.unavailable")}</code></dd></div>
        <div><dt>{t("gameTape.feedback.recordedCandidate")}</dt><dd><code>{feedback.campaign.current_candidate_identity ?? t("gameTape.feedback.unavailable")}</code><small>{t("gameTape.feedback.candidateFreshness")}</small></dd></div>
      </> : null}
    </dl>
    {(feedback.omissions.steps ?? 0) > 0 || (feedback.omissions.goals ?? 0) > 0 || (feedback.omissions.sources ?? 0) > 0 || (feedback.omissions.verifications ?? 0) > 0 || (feedback.omissions.fields?.length ?? 0) > 0 ? <p>{t("gameTape.feedback.omissions", { steps: feedback.omissions.steps ?? 0, goals: feedback.omissions.goals ?? 0, sources: feedback.omissions.sources ?? 0, verification: feedback.omissions.verifications ?? 0 })}</p> : null}
    {feedback.omissions.response_limit_applied ? <p>{t("gameTape.feedback.responseLimit")}</p> : null}
    <details className="advanced-disclosure">
      <summary>{t("gameTape.feedback.details")}</summary>
      {feedback.steps?.length ? <ul>{feedback.steps.map((step) => <li key={step.step_execution_id}>
        <strong>{step.step_id} · {step.diagnostic.classification}</strong>
        <p>{step.diagnostic.reason}</p>
        <small>{t("gameTape.feedback.stepSettlement", { outcome: step.settlement.outcome ?? t("gameTape.feedback.unavailable"), cleanup: step.settlement.cleanup ?? t("gameTape.feedback.unavailable") })}</small>
      </li>)}</ul> : <p>{t("gameTape.feedback.noStepResult")}</p>}
      <section aria-label={t("gameTape.feedback.goals")}>
        <h4>{t("gameTape.feedback.goals")}</h4>
        <ul>{(feedback.goals ?? []).map((goal) => <li key={goal.step_id}><strong>{goal.step_id} · {goal.availability}</strong><p>{goal.summary ?? t("gameTape.feedback.unavailable")}</p>{(goal.omitted_characters ?? 0) > 0 ? <small>{t("gameTape.feedback.omittedCharacters", { count: goal.omitted_characters })}</small> : null}</li>)}</ul>
      </section>
      <section aria-label={t("gameTape.feedback.sources")}>
        <h4>{t("gameTape.feedback.sources")}</h4>
        <ul>{(feedback.sources ?? []).map((source) => <li key={source.repository_id}><strong>{source.repository_id}</strong><p><code>{source.source_authority_id}</code></p><p><code>{source.source_content_sha256}</code></p></li>)}</ul>
      </section>
      <section aria-label={t("gameTape.feedback.verification")}>
        <h4>{t("gameTape.feedback.verification")}</h4>
        <ul>{(feedback.verification ?? []).map((verification) => <li key={verification.repository_id}><strong>{verification.repository_id} · {verification.availability}</strong><p>{t("gameTape.feedback.verificationCounts", { selected: verification.selected_count, actual: verification.actual_count, results: verification.result_count })}</p><small>{verification.qualification}</small></li>)}</ul>
      </section>
      {feedback.budgets ? <dl className="game-tape-receipt">
        <div><dt>{t("gameTape.campaign.maximumRuns")}</dt><dd>{feedback.budgets.maximum_runs ?? t("gameTape.feedback.unavailable")}</dd></div>
        <div><dt>{t("gameTape.campaign.remainingRuns")}</dt><dd>{feedback.budgets.remaining_runs ?? t("gameTape.feedback.unavailable")} · {feedback.budgets.remaining_runs_authority ?? t("gameTape.feedback.unavailable")}</dd></div>
        <div><dt>{t("gameTape.campaign.deadline")}</dt><dd>{feedback.budgets.deadline_at ?? t("gameTape.feedback.unavailable")}</dd></div>
      </dl> : null}
      <section aria-label={t("gameTape.feedback.controls")}>
        <h4>{t("gameTape.feedback.controls")}</h4>
        <p>{t("gameTape.feedback.controlsRule")}</p>
        <p>{feedback.controls.environment_qualification ?? t("gameTape.feedback.unavailable")} · {feedback.controls.qualification_reason}</p>
        <ul>{(feedback.controls.selections ?? []).map((selection) => <li key={selection.step_id}><strong>{selection.step_id} · {selection.agent} · {selection.mode}</strong><p><code>{selection.implementation_identity}</code></p><small>{selection.selected_backend} · {selection.sandbox_owner} · {selection.placement_availability} · {selection.environment_qualification ?? t("gameTape.feedback.unavailable")}</small></li>)}</ul>
        {(feedback.controls.omitted_selections ?? 0) > 0 ? <p>{t("gameTape.feedback.omittedSelections", { count: feedback.controls.omitted_selections })}</p> : null}
        <ul>{feedback.controls.controls.map((control) => <li key={control.action}>
          <strong>{t(`gameTape.feedback.actions.${control.action}`)} · {t(`gameTape.feedback.controlStates.${control.state}`)}</strong>
          <p>{control.reason}</p>
          {control.requirements?.length ? <small>{t("gameTape.feedback.requirements")}: {control.requirements.join(", ")}</small> : null}
        </li>)}</ul>
      </section>
      <details className="advanced-disclosure">
        <summary>{t("gameTape.feedback.structured")}</summary>
        <pre>{JSON.stringify(feedback, null, 2)}</pre>
      </details>
    </details>
  </section>;
}
