import type { FleetRunHistoryItem, FleetRunHistoryPage } from "@generated/models";

/**
 * Validate one durable history continuation page against the exact cursor and
 * already-owned history prefix. This is protocol ownership, not History UI state.
 */
export function validateHistoryContinuation(
  current: FleetRunHistoryItem[],
  requestedCursor: number,
  page: FleetRunHistoryPage,
): void {
  const seenRuns = new Set(current.map((item) => item.run.fleet_run_id));
  const seenRevisions = new Set(current.map((item) => item.admitted_revision));
  for (const item of page.items) {
    if (item.admitted_revision >= requestedCursor) {
      throw new Error(`Fleet Run history page crossed requested cursor ${requestedCursor} with revision ${item.admitted_revision}`);
    }
    if (seenRuns.has(item.run.fleet_run_id)) {
      throw new Error(`Fleet Run history page repeated Run ${item.run.fleet_run_id}`);
    }
    if (seenRevisions.has(item.admitted_revision)) {
      throw new Error(`Fleet Run history page repeated admitted revision ${item.admitted_revision}`);
    }
  }
}
