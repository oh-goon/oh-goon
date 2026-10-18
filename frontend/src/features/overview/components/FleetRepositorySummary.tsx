import { useTranslation } from "react-i18next";

import type { FleetSummary } from "@generated/models";
import { SummaryCard } from "@/components/common/SummaryCard";

interface Props { summary: FleetSummary | null; selectedCount: number; }

export function FleetRepositorySummary({ summary, selectedCount }: Props) {
  const { t } = useTranslation();
  const attention = summary?.needs_attention ?? 0;
  return <section className="summary-grid summary-grid--compact" aria-label={t("summary.ariaLabel")}>
    <SummaryCard label={t("summary.selected")} value={selectedCount} hint={t("summary.selectedHint")} />
    <SummaryCard label={t("summary.ready")} value={summary?.ready_for_update ?? 0} hint={t("summary.readySimpleHint")} tone="success" />
    <SummaryCard label={t("summary.attention")} value={attention} hint={t("summary.attentionSimpleHint")} tone={attention > 0 ? "warning" : "success"} />
  </section>;
}
