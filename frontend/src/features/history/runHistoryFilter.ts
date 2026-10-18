import { FleetRunOutcome } from "@generated/enums";
import type { FleetRunHistoryItem, FleetRunOutcome as FleetRunOutcomeType } from "@generated/models";

export type RunHistoryFilter = "all" | "success" | "attention" | "failed" | "active";
const RUN_HISTORY_FILTERS = new Set<RunHistoryFilter>(["all", "success", "attention", "failed", "active"]);
export function isRunHistoryFilter(value: string | null): value is RunHistoryFilter { return value !== null && RUN_HISTORY_FILTERS.has(value as RunHistoryFilter); }

export function runHistoryFilterOutcome(filter: RunHistoryFilter): FleetRunOutcomeType | null {
  switch (filter) {
    case "success": return FleetRunOutcome.SUCCESS;
    case "attention": return FleetRunOutcome.ATTENTION_REQUIRED;
    case "failed": return FleetRunOutcome.FAILED;
    case "active": return FleetRunOutcome.IN_PROGRESS;
    default: return null;
  }
}

export function searchRunHistory(
  items: readonly FleetRunHistoryItem[],
  query: string,
): FleetRunHistoryItem[] {
  const needle = query.trim().toLowerCase();
  return items.filter((item) => {
    if (!needle) return true;
    const runText = `${item.run.fleet_run_id} ${item.run.capability.action} ${item.outcome}`.toLowerCase();
    if (runText.includes(needle)) return true;
    return item.run.targets.some((target) => `${target.name} ${target.path}`.toLowerCase().includes(needle));
  });
}
