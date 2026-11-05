import type { DependencyPackageOverview, DependencyTargetFilter } from "@generated/models";
import type { DependencyPackageIdentity } from "@/domain/api/ApiClient";

export type DependencyEvidence = NonNullable<DependencyTargetFilter["evidence"]>;
export type DependencyCoverage = NonNullable<DependencyTargetFilter["coverage"]>;
export type { DependencyEcosystem, DependencyPackageIdentity } from "@/domain/api/ApiClient";

type PackageResponseIdentity = Pick<DependencyPackageOverview, "package_name" | "ecosystem" | "query_identity">;

export function matchesDependencyPackageIdentity(requested: DependencyPackageIdentity, received: PackageResponseIdentity): boolean {
  if (requested.ecosystem !== undefined && received.ecosystem !== requested.ecosystem) return false;
  if (received.package_name === requested.packageName) return true;
  const correspondence = received.query_identity;
  return correspondence !== undefined && correspondence !== null
    && correspondence.requested_name === requested.packageName
    && correspondence.canonical_name === received.package_name
    && correspondence.ecosystem === received.ecosystem;
}
