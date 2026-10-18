import { useTranslation } from "react-i18next";

import type { FleetRunHistoryItem } from "@generated/models";
import { actionKey, outcomeKey } from "@/domain/presentation/operations";
import { formatRunDate, runTargetDisplay } from "@/features/history/runHistoryPresentation";

interface Props {
  items: readonly FleetRunHistoryItem[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  onOpenHistory: () => void;
}

export function RunReviewPicker({ items, selectedRunId, onSelect, onOpenHistory }: Props) {
  const { t } = useTranslation();
  if (items.length === 0) return null;
  return <section className="panel run-review-picker" aria-label={t("history.reviewPicker.title")}>
    <div>
      <span className="eyebrow">{t("history.reviewPicker.eyebrow")}</span>
      <strong>{t("history.reviewPicker.title")}</strong>
    </div>
    <label>
      <span>{t("history.reviewPicker.label")}</span>
      <select value={selectedRunId ?? ""} onChange={(event) => { if (event.target.value) onSelect(event.target.value); }}>
        <option value="" disabled>{t("history.reviewPicker.chooseRun")}</option>
        {items.map((item) => {
          const target = runTargetDisplay(item, t("history.repositoryCount", { count: item.run.targets.length }));
          return <option key={item.run.fleet_run_id} value={item.run.fleet_run_id}>
            {target} · {t(outcomeKey(item.outcome))} · {t(actionKey(item.run.capability.action))} · {formatRunDate(item.run.accepted_at)}
          </option>;
        })}
      </select>
    </label>
    <button className="button button--secondary" type="button" onClick={onOpenHistory}>{t("history.reviewPicker.openHistory")}</button>
  </section>;
}
