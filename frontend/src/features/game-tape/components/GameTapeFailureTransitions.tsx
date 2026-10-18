import { useTranslation } from "react-i18next";

import type {
  GameTapeFailureSequenceTransition,
  GameTapeFailureTransitionSummary,
  GameTapeSelectedContextWindow,
} from "@generated/models";
import { ReproductionContextSummary } from "@/features/game-tape/components/GameTapeFailureContext";
import { signedDuration } from "@/features/game-tape/gameTapeFormat";

export function GameTapeFailureTransitions({
  transitions,
  summary,
  contextWindow,
}: {
  transitions: readonly GameTapeFailureSequenceTransition[];
  summary: GameTapeFailureTransitionSummary;
  contextWindow: GameTapeSelectedContextWindow;
}) {
  const { t } = useTranslation();
  return <section className="game-tape-failure-transitions">
    <header>
      <div><strong>{t("gameTape.failureHistory.transitionsTitle")}</strong><small>{t("gameTape.failureHistory.transitionsRule")}</small></div>
      <span>{t("gameTape.failureHistory.transitionsCount", { count: summary.sampled_transition_count })}</span>
    </header>
    <dl className="game-tape-failure-transitions__summary">
      <div><dt>{t("gameTape.failureHistory.statusChanges")}</dt><dd>{summary.status_change_count}</dd></div>
      <div><dt>{t("gameTape.failureHistory.sameContextStatusChanges")}</dt><dd>{summary.same_context_status_changes}</dd></div>
      <div><dt>{t("gameTape.failureHistory.changedContextStatusChanges")}</dt><dd>{summary.changed_context_status_changes}</dd></div>
      <div><dt>{t("gameTape.failureHistory.incompleteContextStatusChanges")}</dt><dd>{summary.incomplete_context_status_changes}</dd></div>
      <div><dt>{t("gameTape.failureHistory.signatureChanges")}</dt><dd>{summary.signature_relation_change_count}</dd></div>
    </dl>
    <ContextWindow window={contextWindow} />
    <div className="game-tape-failure-transitions__rows">
      {transitions.map((transition) => <article key={`${transition.from_run_id}:${transition.to_run_id}`}>
        <header><code>{transition.from_run_id}</code><span aria-hidden="true">→</span><code>{transition.to_run_id}</code></header>
        <div>
          <span>{t("gameTape.failureHistory.transitionStatus", { from: transition.from_status, to: transition.to_status })}</span>
          <span>{transition.status_changed ? t("gameTape.failureHistory.statusChanged") : t("gameTape.failureHistory.statusSame")}</span>
          <span>{signedDuration(transition.duration_delta_ms)}</span>
          <span>{t("gameTape.failureHistory.transitionSignature", {
            from: t(`gameTape.failureHistory.signatureRelation.${transition.from_signature_relation}`),
            to: t(`gameTape.failureHistory.signatureRelation.${transition.to_signature_relation}`),
          })}</span>
        </div>
        <small><ReproductionContextSummary context={transition.context} /></small>
      </article>)}
    </div>
    <small>{t("gameTape.failureHistory.transitionsContract")}</small>
  </section>;
}

function ContextWindow({ window }: { window: GameTapeSelectedContextWindow }) {
  const { t } = useTranslation();
  return <div className="game-tape-failure-context-window">
    <strong>{t("gameTape.failureHistory.contextWindowTitle")}</strong>
    <span>{t("gameTape.failureHistory.contextWindowValue", {
      prior: window.prior_same_context_runs,
      later: window.later_same_context_runs,
      total: window.total_same_context_runs_including_selected,
    })}</span>
    {window.nearest_prior_boundary_run_id ? <small>{t("gameTape.failureHistory.priorContextBoundary", {
      run: window.nearest_prior_boundary_run_id,
      classification: t(`gameTape.failureHistory.contextClassification.${window.nearest_prior_boundary_classification}`),
    })}</small> : null}
    {window.nearest_later_boundary_run_id ? <small>{t("gameTape.failureHistory.laterContextBoundary", {
      run: window.nearest_later_boundary_run_id,
      classification: t(`gameTape.failureHistory.contextClassification.${window.nearest_later_boundary_classification}`),
    })}</small> : null}
  </div>;
}
