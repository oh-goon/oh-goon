import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { FleetRunHistoryItem, FleetRunHistoryPage, FleetRunOutcome, FleetRunStatus, FleetRunSource } from "@generated/models";
import type { ReadFailure, ReadResourceStatus } from "@/domain/reads/ReadResource";
import { historyPageRevision } from "@/domain/reads/readRevision";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { validateHistoryContinuation } from "@/domain/fleet/historyContinuation";
import { projectHistoryAggregation, type HistoryAggregation } from "@/features/history/hooks/historyAggregation";

export interface RunHistoryListState {
  readonly items: FleetRunHistoryItem[];
  readonly status: ReadResourceStatus;
  readonly failure: ReadFailure | null;
  readonly hasMore: boolean;
  refresh(): Promise<FleetRunHistoryPage | null>;
  loadMore(): Promise<void>;
}

export function useRunHistoryList(outcome?: FleetRunOutcome, runStatus?: FleetRunStatus, source?: FleetRunSource, repositoryId?: string, fromAt?: string, toAt?: string, planQuery?: string): RunHistoryListState {
  const client = useApiClient();
  const { t } = useTranslation();
  const queryIdentity = canonicalIdentity({
    outcome: outcome ?? null,
    runStatus: runStatus ?? null,
    source: source ?? null,
    repositoryId: repositoryId ?? null,
    fromAt: fromAt ?? null,
    toAt: toAt ?? null,
    planQuery: planQuery ?? null,
  });
  const firstRequest = useCallback((signal: AbortSignal) => client.fleetRunHistory(undefined, 12, outcome, runStatus, source, repositoryId, fromAt, toAt, planQuery, { signal }), [client, outcome, runStatus, source, repositoryId, fromAt, toAt, planQuery]);
  const first = useReadResource({
    scope: "history-list",
    source: "GET /fleet/runs",
    identity: queryIdentity,
    errorMessage: t("errors.history"),
    request: firstRequest,
    revision: historyPageRevision,
  });
  const firstHeadRevision = first.resource.data !== null ? first.resource.revision : null;
  const [aggregation, setAggregation] = useState<HistoryAggregation>(() => ({
    queryIdentity,
    headRevision: null,
    items: [],
    cursor: null,
  }));
  const ownedAggregation = projectHistoryAggregation(
    aggregation,
    queryIdentity,
    firstHeadRevision,
    first.resource.data,
  );
  const cursor = ownedAggregation.cursor;
  const moreRequest = useCallback(
    (signal: AbortSignal) => client.fleetRunHistory(cursor ?? undefined, 12, outcome, runStatus, source, repositoryId, fromAt, toAt, planQuery, { signal }),
    [client, cursor, outcome, runStatus, source, repositoryId, fromAt, toAt, planQuery],
  );
  const more = useReadResource({
    scope: "history-list-more",
    source: "GET /fleet/runs?before_revision",
    identity: cursor === null ? null : canonicalIdentity({ queryIdentity, cursor }),
    enabled: cursor !== null,
    auto: false,
    errorMessage: t("errors.history"),
    request: moreRequest,
    validate: (page) => {
      if (cursor !== null) validateHistoryContinuation(ownedAggregation.items, cursor, page);
    },
    revision: historyPageRevision,
  });

  useEffect(() => {
    if (first.resource.data === null) return;
    setAggregation({
      queryIdentity,
      headRevision: first.resource.revision,
      items: first.resource.data.items,
      cursor: first.resource.data.next_before_revision ?? null,
    });
  }, [first.resource.revision, first.resource.data, queryIdentity]);

  const loadMore = useCallback(async () => {
    if (cursor === null) return;
    const page = await more.refresh();
    if (page === null) return;
    const expectedHeadRevision = ownedAggregation.headRevision;
    setAggregation((current) => current.queryIdentity !== queryIdentity
      || current.headRevision !== expectedHeadRevision
      ? current
      : {
          queryIdentity,
          headRevision: expectedHeadRevision,
          items: [...current.items, ...page.items],
          cursor: page.next_before_revision ?? null,
        });
  }, [cursor, more, ownedAggregation.headRevision, queryIdentity]);

  const failure = first.resource.failure ?? more.resource.failure;
  const status = more.resource.status === "read-loading" || more.resource.status === "read-refreshing"
    ? more.resource.status
    : first.resource.status;
  return { items: ownedAggregation.items, status, failure, hasMore: cursor !== null, refresh: first.refresh, loadMore };
}

