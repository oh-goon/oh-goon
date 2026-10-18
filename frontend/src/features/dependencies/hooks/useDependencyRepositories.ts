import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { DependencyEvidence, DependencyRelationship } from "@/features/dependencies/model";
import type { ReadResource } from "@/domain/reads/ReadResource";
import type { DependencyRepositoryPage } from "@generated/models";

export interface DependencyRepositoryQuery {
  packageName: string;
  resolvedVersion: string;
  versionSpecifier: string;
  advisoryId: string;
  relationship: DependencyRelationship;
  evidence: DependencyEvidence;
  offset: number;
}

export function useDependencyRepositories(query: DependencyRepositoryQuery): ReadResource<DependencyRepositoryPage> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.dependencyRepositories(
      query.packageName,
      query.resolvedVersion || undefined,
      undefined,
      query.relationship,
      query.evidence,
      query.offset,
      25,
      query.versionSpecifier || undefined,
      query.advisoryId || undefined,
      { signal },
    ),
    [client, query.advisoryId, query.evidence, query.offset, query.packageName, query.relationship, query.resolvedVersion, query.versionSpecifier],
  );
  const identity = query.packageName ? canonicalIdentity(query) : null;
  return useReadResource<DependencyRepositoryPage>({
    scope: "dependency-repositories",
    source: "GET /dependencies/:package/repositories",
    identity,
    enabled: Boolean(query.packageName),
    errorMessage: t("dependencies.errors.repositories"),
    request,
    validate: (value) => {
      if (value.package_name !== query.packageName || value.offset !== query.offset || value.limit !== 25) {
        throw new Error("Dependency repository response does not match the requested package page");
      }
    },
  }).resource;
}
