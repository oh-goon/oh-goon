import {
  FleetRunOutcome as FleetRunOutcomeValue,
  FleetRunStatus as FleetRunStatusValue,
  OperationAction as OperationActionValue,
  OperationTargetKind,
  UvRecipeId as UvRecipeIdValue,
  Verdict as VerdictValue,
} from "@generated/enums";
import type {
  FleetRunOutcome,
  FleetRunStatus,
  OperationAction,
  OperationTarget,
  Verdict,
} from "@generated/models";

export type BadgeTone = "success" | "info" | "warning" | "danger" | "neutral";

const OPERATION_ACTION_KEYS = {
  [OperationActionValue.INIT]: "operations.action.init",
  [OperationActionValue.PLAN]: "operations.action.plan",
  [OperationActionValue.UPDATE]: "operations.action.update",
  [OperationActionValue.STATUS]: "operations.action.status",
  [OperationActionValue.VERIFY]: "operations.action.verify",
  [OperationActionValue.PREPARE_REVIEW]: "operations.action.prepareReview",
  [OperationActionValue.RECIPE]: "operations.action.recipe",
  [OperationActionValue.ADOPT_UV]: "operations.action.adoptUv",
  [OperationActionValue.EXECUTE_PLAN]: "operations.action.executePlan",
} as const satisfies Record<OperationAction, string>;

export function actionKey(action: OperationAction) {
  return OPERATION_ACTION_KEYS[action];
}

export function targetLabel(target: OperationTarget | null | undefined): string | null {
  if (!target) return null;
  if (target.kind === OperationTargetKind.RECIPE) return target.recipe === UvRecipeIdValue.RUN_TASK && target.task ? `${target.recipe}: ${target.task}` : target.recipe;
  if (target.kind === OperationTargetKind.FLEET_PLAN) return target.plan.name;
  return "Adopt UV";
}

export function outcomeTone(outcome: FleetRunOutcome): BadgeTone {
  switch (outcome) {
    case FleetRunOutcomeValue.SUCCESS: return "success";
    case FleetRunOutcomeValue.ATTENTION_REQUIRED: return "warning";
    case FleetRunOutcomeValue.CANCELLED: return "warning";
    case FleetRunOutcomeValue.FAILED: return "danger";
    case FleetRunOutcomeValue.IN_PROGRESS: return "info";
  }
}

export function outcomeKey(outcome: FleetRunOutcome) {
  switch (outcome) {
    case FleetRunOutcomeValue.SUCCESS: return "operations.outcome.SUCCESS" as const;
    case FleetRunOutcomeValue.ATTENTION_REQUIRED: return "operations.outcome.ATTENTION_REQUIRED" as const;
    case FleetRunOutcomeValue.CANCELLED: return "operations.outcome.CANCELLED" as const;
    case FleetRunOutcomeValue.FAILED: return "operations.outcome.FAILED" as const;
    case FleetRunOutcomeValue.IN_PROGRESS: return "operations.outcome.IN_PROGRESS" as const;
  }
}

export function runStatusKey(status: FleetRunStatus) {
  switch (status) {
    case FleetRunStatusValue.QUEUED: return "history.status.QUEUED" as const;
    case FleetRunStatusValue.RUNNING: return "history.status.RUNNING" as const;
    case FleetRunStatusValue.CANCELLING: return "history.status.CANCELLING" as const;
    case FleetRunStatusValue.COMPLETED: return "history.status.COMPLETED" as const;
    case FleetRunStatusValue.CANCELLED: return "history.status.CANCELLED" as const;
    case FleetRunStatusValue.FAILED: return "history.status.FAILED" as const;
  }
}

export function runStatusTone(status: FleetRunStatus): BadgeTone {
  switch (status) {
    case FleetRunStatusValue.COMPLETED: return "success";
    case FleetRunStatusValue.FAILED: return "danger";
    case FleetRunStatusValue.CANCELLED: return "warning";
    case FleetRunStatusValue.CANCELLING: return "warning";
    case FleetRunStatusValue.RUNNING: return "info";
    case FleetRunStatusValue.QUEUED: return "neutral";
  }
}

export function verdictTone(verdict: Verdict): BadgeTone {
  if (verdict === VerdictValue.READY) return "success";
  if (verdict === VerdictValue.UPDATED) return "info";
  if (verdict === VerdictValue.FAILED) return "danger";
  if (verdict === VerdictValue.MANUAL_ACTION_REQUIRED) return "warning";
  return "neutral";
}

export function verdictKey(verdict: Verdict) {
  switch (verdict) {
    case VerdictValue.UNKNOWN: return "repositories.verdict.UNKNOWN" as const;
    case VerdictValue.NOT_CHECKED: return "repositories.verdict.NOT_CHECKED" as const;
    case VerdictValue.READY: return "repositories.verdict.READY" as const;
    case VerdictValue.UPDATED: return "repositories.verdict.UPDATED" as const;
    case VerdictValue.UNCHANGED: return "repositories.verdict.UNCHANGED" as const;
    case VerdictValue.FAILED: return "repositories.verdict.FAILED" as const;
    case VerdictValue.MANUAL_ACTION_REQUIRED: return "repositories.verdict.MANUAL_ACTION_REQUIRED" as const;
    case VerdictValue.EXCLUDED: return "repositories.verdict.EXCLUDED" as const;
  }
}

