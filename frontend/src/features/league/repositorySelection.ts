import type { LeagueMap } from "@generated/models";

export type LeagueRepositoryEntry = NonNullable<LeagueMap["teams"]>[number];

export interface RepositorySelectionView {
  readonly visibleRepositories: readonly LeagueRepositoryEntry[];
  readonly visibleIds: readonly string[];
  readonly selectedCount: number;
  readonly visibleCount: number;
  readonly totalCount: number;
  readonly hiddenSelectedCount: number;
}

function matchesQuery(entry: LeagueRepositoryEntry, query: string): boolean {
  if (!query) return true;
  const repository = entry.repository;
  const haystack = `${repository.name}\n${repository.path}\n${repository.repository_id}`.toLocaleLowerCase();
  return haystack.includes(query);
}

export function repositorySelectionView(
  repositories: readonly LeagueRepositoryEntry[],
  selectedIds: readonly string[],
  query: string,
  selectedOnly: boolean,
): RepositorySelectionView {
  const selected = new Set(selectedIds);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleRepositories = repositories.filter((entry) => {
    const id = entry.repository.repository_id;
    return (!selectedOnly || selected.has(id)) && matchesQuery(entry, normalizedQuery);
  });
  const visibleIds = visibleRepositories.map((team) => team.repository.repository_id);
  const visible = new Set(visibleIds);
  const hiddenSelectedCount = selectedIds.filter((id) => !visible.has(id)).length;
  return {
    visibleRepositories,
    visibleIds,
    selectedCount: selectedIds.length,
    visibleCount: visibleRepositories.length,
    totalCount: repositories.length,
    hiddenSelectedCount,
  };
}

export function setVisibleRepositorySelection(
  selectedIds: readonly string[],
  visibleIds: readonly string[],
  shouldSelect: boolean,
): string[] {
  const visible = new Set(visibleIds);
  if (!shouldSelect) return selectedIds.filter((id) => !visible.has(id));
  const selected = new Set(selectedIds);
  return [...selectedIds, ...visibleIds.filter((id) => !selected.has(id))];
}
