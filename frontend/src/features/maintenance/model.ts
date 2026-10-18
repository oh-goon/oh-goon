import { FleetRepositoryScope, ReadinessState } from "@generated/enums";
import type { FleetRepository, FleetRunState, ReviewPreparation } from "@generated/models";

export type MaintenanceStage = "add-repositories" | "running" | "resolve" | "check" | "preview" | "apply" | "review";

export interface MaintenanceState {
  stage: MaintenanceStage;
  selected: FleetRepository[];
  needsAttention: number;
  reviewableIds: string[];
}

export function maintenanceState(
  repositories: FleetRepository[],
  selectedIds: string[],
  activeRun: FleetRunState | null,
  reviewPreparation: ReviewPreparation | null,
): MaintenanceState {
  const selectedSet = new Set(selectedIds);
  const included = repositories.filter((item) => item.scope === FleetRepositoryScope.INCLUDED);
  const selected = included.filter((item) => selectedSet.has(item.repository.repository_id));
  const ready = selected.filter((item) => item.readiness_state === ReadinessState.READY).length;
  const needsAttention = selected.filter((item) => item.needs_attention).length;
  const currentPlans = selected.filter((item) => item.has_current_plan).length;
  const reviewableSet = new Set((reviewPreparation?.repositories ?? []).map((item) => item.repository_id));
  const reviewableIds = selected.filter((item) => reviewableSet.has(item.repository.repository_id)).map((item) => item.repository.repository_id);

  let stage: MaintenanceStage;
  if (included.length === 0) stage = "add-repositories";
  else if (activeRun !== null) stage = "running";
  else if (needsAttention > 0) stage = "resolve";
  else if (reviewableIds.length > 0) stage = "review";
  else if (selected.length === 0 || ready < selected.length) stage = "check";
  else if (currentPlans < selected.length) stage = "preview";
  else stage = "apply";

  return { stage, selected, needsAttention, reviewableIds };
}
