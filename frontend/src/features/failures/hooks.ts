import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource, type ReadResourceHandle } from "@/app/reads/useReadResource";
import { contentRevision } from "@/domain/reads/readRevision";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { FleetFailureGroupPage, FleetFailureInventory, FleetFailureRepositoryPage } from "@generated/models";

export function useFailureInventory(): ReadResourceHandle<FleetFailureInventory> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback((signal: AbortSignal) => client.fleetFailureInventory({ signal }), [client]);
  return useReadResource({
    scope: "failure-inventory",
    source: "GET /fleet/runs/failures",
    errorMessage: t("errors.history"),
    request,
    revision: contentRevision,
  });
}

export function useFailureGroups(query: string, offset: number, limit = 50): ReadResourceHandle<FleetFailureGroupPage> {
  const client = useApiClient();
  const { t } = useTranslation();
  const identity = canonicalIdentity({ query, offset, limit });
  const request = useCallback(
    (signal: AbortSignal) => client.fleetFailureGroups(query || undefined, offset, limit, { signal }),
    [client, limit, offset, query],
  );
  return useReadResource({
    scope: "failure-groups",
    source: "GET /fleet/runs/failures/groups",
    identity,
    errorMessage: t("errors.history"),
    request,
    validate: (value) => {
      if (value.offset !== offset || value.limit !== limit) {
        throw new Error(`Failure group page coordinates do not match offset=${offset}, limit=${limit}`);
      }
    },
    revision: contentRevision,
  });
}

export function useFailureRepositories(groupId: string | null, query: string, signatureId: string | null, offset: number, limit = 50): ReadResourceHandle<FleetFailureRepositoryPage> {
  const client = useApiClient();
  const { t } = useTranslation();
  const identity = groupId ? canonicalIdentity({ groupId, query, signatureId, offset, limit }) : null;
  const request = useCallback(
    (signal: AbortSignal) => {
      if (!groupId) throw new Error("Failure group is required");
      return client.fleetFailureRepositories(groupId, query || undefined, signatureId || undefined, offset, limit, { signal });
    },
    [client, groupId, limit, offset, query, signatureId],
  );
  return useReadResource({
    scope: "failure-repositories",
    source: "GET /fleet/runs/failures/groups/{group_id}/repositories",
    identity,
    enabled: groupId !== null,
    errorMessage: t("errors.history"),
    request,
    validate: (value) => {
      if (groupId === null) return;
      if (value.group.group_id !== groupId || value.offset !== offset || value.limit !== limit) {
        throw new Error(`Failure repository page identity mismatch for group ${groupId}`);
      }
      for (const item of value.items ?? []) {
        if (item.group_id !== groupId) throw new Error(`Failure repository item escaped requested group ${groupId}`);
      }
    },
    revision: contentRevision,
  });
}
