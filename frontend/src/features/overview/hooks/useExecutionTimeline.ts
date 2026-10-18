import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { FleetExecutionTimeline } from "@generated/models";
import type { ExecutionObservationBucket } from "@/features/overview/executionObservationWindow";

export function useExecutionTimeline(
  bucket: ExecutionObservationBucket,
  fromAt: string,
  toAt: string,
  planQuery: string,
  repositoryQuery: string,
  offset: number,
  live: boolean,
): ReadResource<FleetExecutionTimeline> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.fleetRunExecutionObservations({
      from_at: fromAt,
      to_at: toAt,
      bucket,
      offset,
      limit: 100,
      ...(planQuery.trim() ? { plan_query: planQuery.trim() } : {}),
      ...(repositoryQuery.trim() ? { repository_query: repositoryQuery.trim() } : {}),
    }, { signal }),
    [bucket, client, fromAt, offset, planQuery, repositoryQuery, toAt],
  );
  return useReadResource<FleetExecutionTimeline>({
    scope: "execution-observations",
    source: "GET /fleet/runs/observations",
    identity: canonicalIdentity({ bucket, fromAt, toAt, planQuery, repositoryQuery, offset }),
    errorMessage: t("executionOverview.timeline.error"),
    request,
    validate: (value) => {
      if (value.bucket !== bucket || Date.parse(value.from_at) !== Date.parse(fromAt) || Date.parse(value.to_at) !== Date.parse(toAt) || value.offset !== offset || value.limit !== 100) {
        throw new Error(`Execution timeline response coordinates do not match the requested window`);
      }
    },
    pollIntervalMs: 5_000,
    shouldPoll: () => live,
  }).resource;
}
