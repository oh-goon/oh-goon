import { useTranslation } from "react-i18next";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { GameTapeFailureSequence } from "@generated/models";
import { ReproductionContextSummary } from "@/features/game-tape/components/GameTapeFailureContext";
import { GameTapeFailureTransitions } from "@/features/game-tape/components/GameTapeFailureTransitions";
import { duration, statusTone } from "@/features/game-tape/gameTapeFormat";

export function GameTapeFailureSequenceView({ sequence }: { sequence: GameTapeFailureSequence }) {
  const { t } = useTranslation();
  const summary = sequence.summary;
  return <section className="game-tape-failure-sequence">
    <header>
      <div><strong>{t("gameTape.failureHistory.sequenceTitle")}</strong><small>{t("gameTape.failureHistory.sequenceRule", { limit: sequence.sample_limit })}</small></div>
      <span>{t("gameTape.failureHistory.sequenceComparable", {
        prior: summary.prior_comparable_runs,
        later: summary.later_comparable_runs,
      })}</span>
    </header>
    <dl className="game-tape-failure-sequence__summary">
      <div><dt>{t("gameTape.failureHistory.latestComparable")}</dt><dd><code>{summary.latest_comparable_run_id}</code><StatusBadge label={t(`gameTape.status.${summary.latest_comparable_status}`)} tone={statusTone(summary.latest_comparable_status)} /></dd></div>
      <div><dt>{t("gameTape.failureHistory.contextEvidenceCoverage")}</dt><dd>{t("gameTape.failureHistory.contextEvidenceCoverageValue", { complete: summary.complete_context_entries, incomplete: summary.incomplete_context_entries })}</dd></div>
      <div><dt>{t("gameTape.failureHistory.sameContextLaterPasses")}</dt><dd>{summary.later_same_context_passes}</dd></div>
      <div><dt>{t("gameTape.failureHistory.sameContextLaterExactFailures")}</dt><dd>{summary.later_same_context_exact_failures}</dd></div>
      <div><dt>{t("gameTape.failureHistory.sameContextRecurrenceAfterPass")}</dt><dd>{summary.exact_failure_recurred_in_same_context_after_same_context_pass ? t("common.yes") : t("common.no")}</dd></div>
    </dl>
    <div className="game-tape-failure-sequence__entries">
      {(sequence.entries ?? []).map((entry) => <article key={`${entry.position}:${entry.fleet_run_id}`} data-position={entry.position}>
        <div className="game-tape-failure-sequence__marker" aria-hidden="true" />
        <div>
          <header><strong><code>{entry.fleet_run_id}</code></strong><span>{new Date(entry.accepted_at).toLocaleString()}</span></header>
          <div className="game-tape-failure-sequence__facts">
            <StatusBadge label={t(`gameTape.status.${entry.status}`)} tone={statusTone(entry.status)} />
            <span>{t(`gameTape.failureHistory.sequencePosition.${entry.position}`)}</span>
            <span>{t(`gameTape.failureHistory.signatureRelation.${entry.signature_relation}`)}</span>
            <span>{duration(entry.duration_ms)}</span>
          </div>
          <small><ReproductionContextSummary context={entry.context} /></small>
        </div>
      </article>)}
    </div>
    <GameTapeFailureTransitions transitions={sequence.transitions ?? []} summary={sequence.transition_summary} contextWindow={sequence.selected_context_window} />
    <small>{t("gameTape.failureHistory.sequenceContract")}</small>
  </section>;
}
