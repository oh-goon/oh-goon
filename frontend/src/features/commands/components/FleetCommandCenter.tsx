import { useState } from "react";
import { useTranslation } from "react-i18next";

import { CommandSafety, OperationAction, OperationTargetKind, UvRecipeId } from "@generated/enums";
import type { FleetCommandCenter as CommandCenterModel, OperationTarget } from "@generated/models";
import { StatusBadge } from "@/components/common/StatusBadge";
import { outcomeKey, outcomeTone } from "@/domain/presentation/operations";

interface Props {
  center: CommandCenterModel | null;
  actionsDisabled: boolean;
  onRun: (target: OperationTarget, repositoryIds: string[]) => void;
}

export function FleetCommandCenter({ center, actionsDisabled, onRun }: Props) {
  const { t } = useTranslation();
  const [task, setTask] = useState("");
  const rows = center?.recipes ?? [];
  const repositoryIds = center?.repository_ids ?? [];

  return (
    <section className="panel control-plane-panel" aria-label={t("commands.title")}>
      <div className="section-heading"><div>
        <span className="eyebrow">{t("commands.eyebrow")}</span>
        <h2>{t("commands.title")}</h2>
        <p>{t("commands.description")}</p>
      </div></div>
      <div className="recipe-grid">
        {rows.map((row) => (
          <article className="recipe-card" key={row.spec.recipe}>
            <div className="recipe-card-heading">
              <div><strong>{row.spec.label}</strong><small>{row.spec.description}</small></div>
              <StatusBadge label={row.spec.safety} tone={row.spec.safety === CommandSafety.READ_ONLY ? "success" : "warning"} />
            </div>
            <div className="recipe-metrics">
              <span><strong>{row.successful}</strong>{t("commands.passed")}</span>
              <span><strong>{row.needs_attention}</strong>{t("commands.attention")}</span>
              <span><strong>{row.not_run}</strong>{t("commands.notRun")}</span>
            </div>
            {row.latest_outcome ? <StatusBadge label={t(outcomeKey(row.latest_outcome))} tone={outcomeTone(row.latest_outcome)} /> : null}
            <div className="action-row">
              {(row.attention_repository_ids ?? []).length > 0 ? (
                <button className="button button--primary" type="button" disabled={actionsDisabled}
                  onClick={() => onRun({ kind: OperationTargetKind.RECIPE, recipe: row.spec.recipe, args: [] }, (row.attention_repository_ids ?? []))}>
                  {t("commands.rerunAttention", { count: (row.attention_repository_ids ?? []).length })}
                </button>
              ) : null}
              <button className={(row.attention_repository_ids ?? []).length > 0 ? "button button--secondary" : "button button--primary"} type="button" disabled={actionsDisabled || row.repositories === 0}
                onClick={() => onRun({ kind: OperationTargetKind.RECIPE, recipe: row.spec.recipe, args: [] }, repositoryIds)}>
                {t("commands.runAll", { count: row.repositories })}
              </button>
            </div>
          </article>
        ))}
      </div>
      <div className="task-runner">
        <div><strong>{t("commands.taskTitle")}</strong><small>{t("commands.taskDescription")}</small></div>
        <label><span>{t("commands.taskExecutable")}</span><input value={task} onChange={(event) => setTask(event.target.value)} placeholder={t("commands.taskPlaceholder")} /></label>
        <button className="button button--primary" type="button" disabled={actionsDisabled || repositoryIds.length === 0 || !task.trim()}
          onClick={() => onRun({ kind: OperationTargetKind.RECIPE, recipe: UvRecipeId.RUN_TASK, task: task.trim(), args: [] }, repositoryIds)}>
          {t("commands.runTask")}
        </button>
      </div>
      <small className="panel-footnote">{t("commands.executionNote", { action: OperationAction.RECIPE })}</small>
    </section>
  );
}
