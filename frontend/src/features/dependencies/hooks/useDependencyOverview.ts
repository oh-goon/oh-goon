import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { ReadResource } from "@/domain/reads/ReadResource";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";
import { matchesDependencyPackageIdentity, type DependencyPackageIdentity } from "@/features/dependencies/model";
import type { DependencyPackageOverview } from "@generated/models";

export function useDependencyOverview({ packageName, ecosystem }: DependencyPackageIdentity): ReadResource<DependencyPackageOverview> {
  const client = useApiClient();
  const { t } = useTranslation();
  const request = useCallback(
    (signal: AbortSignal) => client.dependencyOverview({ packageName, ecosystem }, { signal }),
    [client, packageName, ecosystem],
  );
  return useReadResource<DependencyPackageOverview>({
    scope: "dependency-overview",
    source: "GET /dependencies/:package",
    identity: packageName ? canonicalIdentity({ packageName, ecosystem }) : null,
    enabled: Boolean(packageName),
    errorMessage: t("dependencies.errors.overview"),
    request,
    validate: (value) => {
      if (!matchesDependencyPackageIdentity({ packageName, ecosystem }, value)) throw new Error("Dependency overview response does not match the requested package identity");
    },
  }).resource;
}
