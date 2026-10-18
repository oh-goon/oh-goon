import { describe, expect, it } from "vitest";

import { scoutRouteFromHash, scoutRouteWithHours } from "@/features/scout/ScoutFeature";

describe("Scout route population state", () => {
  it("drops pagination when the observation window changes", () => {
    expect(scoutRouteWithHours(
      { view: "findings", hours: 720, category: "HEAVY_HITTER", offset: 80 },
      24,
    )).toEqual({ view: "findings", hours: 24, category: "HEAVY_HITTER" });
  });

  it("drops detail offset without a valid section", () => {
    expect(scoutRouteFromHash("#scout?view=repository&repository_id=repo-a&hours=24&offset=200"))
      .toEqual({ view: "repository", hours: 24, repositoryId: "repo-a" });
  });

  it("canonicalizes sections to the detail view that owns them", () => {
    expect(scoutRouteFromHash("#scout?view=repository&repository_id=repo-a&section=versions&offset=200"))
      .toEqual({ view: "repository", hours: 24, repositoryId: "repo-a" });
    expect(scoutRouteFromHash("#scout?view=goon&goon=python/test&section=versions&offset=200"))
      .toEqual({ view: "goon", hours: 24, goonName: "python/test", section: "versions", offset: 200 });
  });
});