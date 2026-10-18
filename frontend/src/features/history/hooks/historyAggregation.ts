import type { FleetRunHistoryItem, FleetRunHistoryPage } from "@generated/models";

export interface HistoryAggregation {
  readonly queryIdentity: string;
  readonly headRevision: string | null;
  readonly items: FleetRunHistoryItem[];
  readonly cursor: number | null;
}

/**
 * Project the accepted first page immediately. React effects persist aggregation for
 * pagination, but effect timing must never create a temporary authoritative-empty
 * history after the read itself is already fresh.
 */
export function projectHistoryAggregation(
  aggregation: HistoryAggregation,
  queryIdentity: string,
  firstHeadRevision: string | null,
  firstPage: FleetRunHistoryPage | null,
): HistoryAggregation {
  if (aggregation.queryIdentity === queryIdentity && aggregation.headRevision === firstHeadRevision) {
    return aggregation;
  }
  if (firstPage !== null) {
    return {
      queryIdentity,
      headRevision: firstHeadRevision,
      items: firstPage.items,
      cursor: firstPage.next_before_revision ?? null,
    };
  }
  return { queryIdentity, headRevision: firstHeadRevision, items: [], cursor: null };
}
