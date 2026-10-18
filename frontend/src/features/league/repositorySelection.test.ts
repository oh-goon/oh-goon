import { describe, expect, it } from "vitest";
import type { LeagueMap } from "@generated/models";
import { setVisibleRepositorySelection, repositorySelectionView } from "@/features/league/repositorySelection";

function teams(): NonNullable<LeagueMap["teams"]> {
  return [
    { repository: { repository_id: "a", name: "Alpha", path: "/league/alpha" }, pipeline: { state: "CONFIGURED" } },
    { repository: { repository_id: "b", name: "Beta", path: "/league/backend-beta" }, pipeline: { state: "MISSING" } },
    { repository: { repository_id: "c", name: "Gamma", path: "/league/gamma" }, pipeline: { state: "INVALID" } },
  ] as NonNullable<LeagueMap["teams"]>;
}

describe("repositorySelectionView", () => {
  it("filters by repository name or path without changing selection", () => {
    const view = repositorySelectionView(teams(), ["a", "c"], "backend", false);
    expect(view.visibleIds).toEqual(["b"]);
    expect(view.selectedCount).toBe(2);
    expect(view.hiddenSelectedCount).toBe(2);
  });

  it("selected-only is a presentation filter", () => {
    const view = repositorySelectionView(teams(), ["c", "a"], "", true);
    expect(view.visibleIds).toEqual(["a", "c"]);
    expect(view.selectedCount).toBe(2);
  });

  it("preserves League order for visible repositories", () => {
    expect(repositorySelectionView(teams(), ["c"], "a", false).visibleIds).toEqual(["a", "b", "c"]);
  });

  it("reports selected, visible, total and hidden selected counts", () => {
    expect(repositorySelectionView(teams(), ["a", "c"], "alpha", false)).toMatchObject({ selectedCount: 2, visibleCount: 1, totalCount: 3, hiddenSelectedCount: 1 });
  });

  it("keeps a 5,000 repository population searchable without changing explicit selection", () => {
    const fleet = Array.from({ length: 5_000 }, (_, index) => ({
      repository: { repository_id: `repo-${index}`, name: `service-${String(index).padStart(5, "0")}`, path: `/fleet/service-${String(index).padStart(5, "0")}` },
      pipeline: { state: "CONFIGURED" },
    })) as NonNullable<LeagueMap["teams"]>;
    const selected = ["repo-1", "repo-4999"];
    const view = repositorySelectionView(fleet, selected, "service-04999", false);
    expect(view.visibleIds).toEqual(["repo-4999"]);
    expect(view).toMatchObject({ selectedCount: 2, visibleCount: 1, totalCount: 5_000, hiddenSelectedCount: 1 });
  });
});

describe("setVisibleRepositorySelection", () => {
  it("selects visible repositories without dropping hidden selections", () => {
    expect(setVisibleRepositorySelection(["c"], ["a", "b"], true)).toEqual(["c", "a", "b"]);
  });

  it("clears only visible repositories", () => {
    expect(setVisibleRepositorySelection(["a", "b", "c"], ["a", "b"], false)).toEqual(["c"]);
  });
});
