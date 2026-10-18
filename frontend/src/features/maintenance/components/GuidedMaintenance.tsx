import { useTranslation } from "react-i18next";

import { OperationAction } from "@generated/enums";
import type { FleetRepository, FleetRunState, OperationAction as OperationActionType, OperationTarget, ReviewPreparation } from "@generated/models";
import { maintenanceState } from "@/features/maintenance/model";

interface Props {
  repositories: FleetRepository[];
  selectedIds: string[];
  activeRun: FleetRunState | null;
  reviewPreparation: ReviewPreparation | null;
  onRun: (action: OperationActionType, options?: { target?: OperationTarget | null; repositoryIds?: string[] }) => void;
  onShowIssues: () => void;
  onAddRepositories: () => void;
}

const STEPS = ["check", "preview", "apply", "review"] as const;

export function GuidedMaintenance({ repositories, selectedIds, activeRun, reviewPreparation, onRun, onShowIssues, onAddRepositories }: Props) {
  const { t } = useTranslation();
  const state = maintenanceState(repositories, selectedIds, activeRun, reviewPreparation);
  const selectedCount = state.selected.length;
  const stageIndex = STEPS.indexOf(state.stage as (typeof STEPS)[number]);

  const primary = (() => {
    switch (state.stage) {
      case "add-repositories": return { label: t("maintenance.actions.addRepositories"), action: onAddRepositories };
      case "resolve": return { label: t("maintenance.actions.viewIssues", { count: state.needsAttention }), action: onShowIssues };
      case "check": return { label: t("maintenance.actions.check", { count: selectedCount }), action: () => onRun(OperationAction.INIT, { repositoryIds: selectedIds }) };
      case "preview": return { label: t("maintenance.actions.preview", { count: selectedCount }), action: () => onRun(OperationAction.PLAN, { repositoryIds: selectedIds }) };
      case "apply": return { label: t("maintenance.actions.update", { count: selectedCount }), action: () => onRun(OperationAction.UPDATE, { repositoryIds: selectedIds }) };
      case "review": return { label: t("maintenance.actions.review", { count: state.reviewableIds.length }), action: () => onRun(OperationAction.PREPARE_REVIEW, { target: reviewPreparation?.target ?? null, repositoryIds: state.reviewableIds }) };
      case "running": return null;
    }
  })();

  return (
    <section className="panel maintenance-panel" aria-labelledby="maintenance-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">{t("maintenance.eyebrow")}</span>
          <h2 id="maintenance-title">{t("maintenance.title")}</h2>
          <p>{t("maintenance.description", { count: selectedCount })}</p>
        </div>
      </div>
      <ol className="maintenance-steps">
        {STEPS.map((step, index) => {
          const complete = stageIndex > index || state.stage === "review" && index < 3;
          const current = state.stage === step;
          return <li className={current ? "maintenance-step maintenance-step--current" : complete ? "maintenance-step maintenance-step--complete" : "maintenance-step"} key={step}>
            <span>{complete ? "✓" : index + 1}</span><div><strong>{t(`maintenance.steps.${step}.title`)}</strong><small>{t(`maintenance.steps.${step}.description`)}</small></div>
          </li>;
        })}
      </ol>
      <div className="next-action-card">
        <span className="eyebrow">{t("maintenance.nextAction")}</span>
        <strong>{t(`maintenance.stage.${state.stage}.title`, { count: selectedCount, issues: state.needsAttention })}</strong>
        <p>{t(`maintenance.stage.${state.stage}.message`, { count: selectedCount, issues: state.needsAttention })}</p>
        {primary ? <button className="button button--primary" type="button" disabled={selectedCount === 0 && state.stage !== "add-repositories"} onClick={primary.action}>{primary.label}</button> : null}
      </div>
    </section>
  );
}
