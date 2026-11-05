import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import type { ReadResource } from "@/domain/reads/ReadResource";
import { matchesDependencyPackageIdentity, type DependencyPackageIdentity } from "@/features/dependencies/model";
import type { DependencyAdvisoryOverview } from "@generated/models";

export interface DependencyAdvisoryQuery extends DependencyPackageIdentity { advisoryId: string; affectedSpecifier: string; }

export function useDependencyAdvisory(query: DependencyAdvisoryQuery): ReadResource<DependencyAdvisoryOverview> {
  const { packageName, ecosystem, advisoryId, affectedSpecifier } = query;
  const client = useApiClient();
  const { t } = useTranslation();
  const enabled = Boolean(packageName && advisoryId && affectedSpecifier);
  const request = useCallback(
    (signal: AbortSignal) => client.dependencyAdvisory(
      { packageName, ecosystem }, advisoryId, affectedSpecifier, { signal },
    ),
    [advisoryId, affectedSpecifier, client, packageName, ecosystem],
  );
  return useReadResource<DependencyAdvisoryOverview>({
    scope: "dependency-advisory",
    source: "GET /dependencies/:package/advisory",
    identity: enabled ? canonicalIdentity({ packageName, ecosystem, advisoryId, affectedSpecifier }) : null,
    enabled,
    errorMessage: t("dependencies.errors.advisory"),
    request,
    validate: (value) => {
      if (!matchesDependencyPackageIdentity(query, value) || value.advisory_id !== advisoryId || value.affected_specifier !== affectedSpecifier) {
        throw new Error("Dependency advisory response does not match the requested advisory identity");
      }
    },
  }).resource;
}
