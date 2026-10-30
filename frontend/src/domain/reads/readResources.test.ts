import { describe, expect, it } from "vitest";

import {
  readScopesInvalidatedByFleetProgress,
  readScopesInvalidatedByMutation,
  readScopesForResources,
  resourcesInvalidatedByMutation,
} from "@/domain/reads/readResources";

describe("read resource invalidation", () => {
  it("invalidates runtime health after every durable mutation scope", () => {
    for (const scope of ["fleet-run", "locations", "repositories", "schedules", "recovery"] as const) {
      expect(resourcesInvalidatedByMutation(scope)).toContain("runtime-health");
    }
  });

  it("keeps plan source edits outside durable runtime mutation causality", () => {
    expect(resourcesInvalidatedByMutation("plans")).toEqual([
      "shared-plans",
      "plan-templates",
    ]);
  });

  it("derives affected read scopes from resources rather than feature tables", () => {
    expect(readScopesForResources(["security-projection"])).toEqual(["fleet-security"]);
    expect(new Set(readScopesForResources(["run-history"]))).toEqual(new Set([
      "history-list",
      "history-detail",
      "agent-campaign",
      "game-tape-diff",
      "game-tape-performance",
      "game-tape-artifacts",
      "game-tape-failure-history",
      "history-list-more",
      "fleet-run-events",
      "fleet-run-detail",
      "recovery",
      "shared-plans",
    ]));
  });

  it("invalidates run-specific Scout evidence with Scout observations", () => {
    expect(readScopesForResources(["scout-observations"])).toContain("scout-run-evidence");
  });

  it("keeps independent schedule reads on distinct scopes while sharing schedule invalidation", () => {
    expect(readScopesForResources(["schedule-state"])).toEqual(expect.arrayContaining([
      "schedules",
      "schedule-occurrence-repositories",
    ]));
  });

  it("makes schedule mutation invalidation the one Schedule catalog refresh owner", () => {
    expect(resourcesInvalidatedByMutation("schedules")).toEqual(expect.arrayContaining([
      "schedule-state",
    ]));
    expect(readScopesInvalidatedByMutation("schedules")).toContain("schedules");
  });

  it("treats Fleet-run mutations as Schedule mutations too", () => {
    expect(resourcesInvalidatedByMutation("fleet-run")).toEqual(expect.arrayContaining([
      "fleet-projection",
      "run-history",
      "execution-overview",
      "schedule-state",
      "scout-observations",
    ]));
  });

  it("invalidates lineup catalog and inspection through one lineup resource", () => {
    expect(readScopesForResources(["league-lineups"])).toEqual(expect.arrayContaining([
      "league-lineups",
      "league-lineup-inspection",
    ]));
  });

  it("refreshes live League-derived previews when Fleet or League facts change", () => {
    expect(readScopesForResources(["fleet-projection"])).toContain("shared-plan-preview");
    expect(readScopesForResources(["league-map"])).toEqual(expect.arrayContaining([
      "league-map",
      "shared-plan-preview",
      "league-lineup-inspection",
    ]));
  });

  it("refreshes Shared Plan catalog run facts when Run history changes", () => {
    expect(readScopesForResources(["run-history"])).toContain("shared-plans");
  });

  it("refreshes every derived population after League location changes", () => {
    expect(resourcesInvalidatedByMutation("locations")).toEqual(expect.arrayContaining([
      "league-map", "league-lineups", "fleet-projection", "security-projection", "migration-projection", "dependency-observations",
    ]));
  });

  it("refreshes migration eligibility after repository scope changes", () => {
    expect(resourcesInvalidatedByMutation("repositories")).toEqual(expect.arrayContaining([
      "fleet-projection", "security-projection", "migration-projection", "dependency-observations",
    ]));
  });

  it("preserves recovery and authoring mutation fan-out ownership", () => {
    expect(resourcesInvalidatedByMutation("recovery")).toEqual(expect.arrayContaining([
      "recovery-state", "run-history",
    ]));
    expect(resourcesInvalidatedByMutation("league-lineups")).toEqual(["league-lineups"]);
    expect(resourcesInvalidatedByMutation("dependency-selections")).toEqual(["dependency-selections"]);
  });

  it("routes Fleet progress through the same read invalidation authority", () => {
    expect(readScopesInvalidatedByFleetProgress()).toEqual(expect.arrayContaining([
      "fleet",
      "history-list",
      "schedules",
      "fleet-security",
      "migration-preview",
      "dependency-search",
      "dependency-overview",
      "dependency-repositories",
      "repository-dependencies",
      "executor-operations",
      "executor-placement-queue",
    ]));
  });
});
