import { useTranslation } from "react-i18next";

import type {
  GameTapeFailureContextCounts,
  GameTapeFailureHistory,
  GameTapeReproductionContextComparison,
} from "@generated/models";

export function ReproductionContextSummary({ context }: { context: GameTapeReproductionContextComparison }) {
  const { t } = useTranslation();
  const summary = context.summary;
  return <span>
    {t(`gameTape.failureHistory.contextClassification.${summary.classification}`)} · {t("gameTape.failureHistory.contextCoverage", {
      known: summary.known_dimensions,
      total: summary.total_dimensions,
    })}
  </span>;
}

export function FailureContextEvidence({ evolution }: { evolution: GameTapeFailureHistory["evolution"] }) {
  const { t } = useTranslation();
  return <div className="game-tape-failure-contexts">
    <strong>{t("gameTape.failureHistory.contextEvidenceTitle")}</strong>
    <p>{t("gameTape.failureHistory.contextEvidenceRule")}</p>
    <div className="game-tape-failure-contexts__grid">
      <ContextCounts label={t("gameTape.failureHistory.priorExactContexts")} counts={evolution.prior_exact_contexts} />
      <ContextCounts label={t("gameTape.failureHistory.laterExactContexts")} counts={evolution.later_exact_contexts} />
      <ContextCounts label={t("gameTape.failureHistory.laterPassContexts")} counts={evolution.later_pass_contexts} />
    </div>
  </div>;
}

function ContextCounts({ label, counts }: { label: string; counts: GameTapeFailureContextCounts }) {
  const { t } = useTranslation();
  return <section>
    <strong>{label}</strong>
    <dl>
      <div><dt>{t("gameTape.failureHistory.contextClassification.SAME_CONTEXT")}</dt><dd>{counts.same_context}</dd></div>
      <div><dt>{t("gameTape.failureHistory.contextClassification.CHANGED_CONTEXT")}</dt><dd>{counts.changed_context}</dd></div>
      <div><dt>{t("gameTape.failureHistory.contextClassification.INCOMPLETE_CONTEXT")}</dt><dd>{counts.incomplete_context}</dd></div>
    </dl>
  </section>;
}
