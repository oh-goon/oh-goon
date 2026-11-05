import { describe, expect, it } from "vitest";

import { dependencyQuery, dependencyReviewStateFromHash } from "@/app/pages/dependencyReviewState";

describe("dependencyReviewState", () => {
  it("round-trips observed matching filters in URL state", () => {
    const query = dependencyQuery({
      q: "mlflow",
      packageName: "mlflow",
      versionSpecifier: "<2.18.0",
      advisoryId: "CVE-2026-MLFLOW",
      evidence: "current",
      coverage: "matching",
      offset: 25,
      repositoryId: "repo-payments",
    });
    const state = dependencyReviewStateFromHash(`#dependencies?${new URLSearchParams(query)}`);

    expect(state).toMatchObject({
      q: "mlflow",
      packageName: "mlflow",
      versionSpecifier: "<2.18.0",
      advisoryId: "CVE-2026-MLFLOW",
      evidence: "current",
      coverage: "matching",
      offset: 25,
      repositoryId: "repo-payments",
    });
  });

  it("drops incompatible match-only filters for evidence-gap populations", () => {
    const state = dependencyReviewStateFromHash(
      "#dependencies?q=mlflow&package=mlflow&coverage=no_current_evidence&version=2.17.2&affected=%3C2.18.0&advisory=CVE-OLD&relationship=transitive&evidence=stale",
    );

    expect(state.coverage).toBe("no_current_evidence");
    expect(state.resolvedVersion).toBe("");
    expect(state.versionSpecifier).toBe("");
    expect(state.advisoryId).toBe("");
    expect(state.evidence).toBe("all");
  });

  it("serializes a non-default coverage population explicitly", () => {
    expect(dependencyQuery({ packageName: "mlflow", coverage: "absent_from_lock" })).toEqual({
      package: "mlflow",
      coverage: "absent_from_lock",
    });
  });

  it("keeps npm scopes and same-name ecosystem selections separate in URLs", () => {
    const scoped = dependencyQuery({ packageName: "@scope/shared", ecosystem: "npm", coverage: "matching" });
    expect(dependencyReviewStateFromHash(`#dependencies?${new URLSearchParams(scoped)}`)).toMatchObject({ packageName: "@scope/shared", ecosystem: "npm" });
    const python = dependencyQuery({ packageName: "shared", ecosystem: "pypi" });
    const npm = dependencyQuery({ packageName: "shared", ecosystem: "npm" });
    expect(python).not.toEqual(npm);
    expect(dependencyReviewStateFromHash("#dependencies?package=shared").ecosystem).toBeUndefined();
  });

  it("does not reinterpret an unsupported URL namespace as another package", () => {
    expect(dependencyReviewStateFromHash("#dependencies?package=shared&ecosystem=unsupported").packageName).toBe("");
  });
});
