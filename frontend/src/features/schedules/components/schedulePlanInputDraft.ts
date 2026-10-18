import type { ExecutionPlanInputValues } from "@/features/plans/PlansFeature";

// Values belong to one exact Plan document. Never write fetched defaults into an
// already edited draft: a delayed passive effect can clobber a user's input.
export interface SchedulePlanInputDraft {
  readonly documentIdentity: string;
  readonly values: ExecutionPlanInputValues;
}

export function schedulePlanInputValues(
  documentIdentity: string | null,
  defaults: Readonly<ExecutionPlanInputValues>,
  draft: SchedulePlanInputDraft | null,
): Readonly<ExecutionPlanInputValues> {
  return documentIdentity !== null && draft?.documentIdentity === documentIdentity ? draft.values : defaults;
}

export function editSchedulePlanInput(
  documentIdentity: string,
  defaults: Readonly<ExecutionPlanInputValues>,
  previous: SchedulePlanInputDraft | null,
  name: string,
  value: string | null,
): SchedulePlanInputDraft {
  return {
    documentIdentity,
    values: { ...schedulePlanInputValues(documentIdentity, defaults, previous), [name]: value },
  };
}
