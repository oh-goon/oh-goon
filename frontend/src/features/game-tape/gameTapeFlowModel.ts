import { GameTapeStatus as GameTapeStatusValue } from "@generated/enums";
import type { GameTapeRepositoryExecution, GameTapeStepExecution, GameTapeStatus } from "@generated/models";

export type GameTapeFlowSourceKind = "goon" | "source" | "resolved";

export interface GameTapeFlowGroup {
  key: string;
  kind: GameTapeFlowSourceKind;
  source: string | null;
  displayName: string | null;
  laneStartIndex: number;
  steps: readonly GameTapeStepExecution[];
  statusCounts: readonly GameTapeFlowStatusCount[];
}

export interface GameTapeFlowStatusCount {
  status: GameTapeStatus;
  count: number;
}


export interface GameTapeFlowSegmentAnchor {
  repositoryExecutionId: string;
  groupKey: string;
  firstStepExecutionId: string;
}

export interface GameTapeFlowSearchMatch {
  repositoryExecutionId: string;
  stepExecutionId: string;
}

export interface GameTapeFlowSelectionContext {
  repository: GameTapeRepositoryExecution;
  repositoryIndex: number;
  repositoryCount: number;
  group: GameTapeFlowGroup;
  step: GameTapeStepExecution;
  laneStepIndex: number;
  laneStepCount: number;
  previousStepExecutionId: string | null;
  nextStepExecutionId: string | null;
  segmentIndex: number;
  segmentCount: number;
  previousSegmentFirstStepExecutionId: string | null;
  nextSegmentFirstStepExecutionId: string | null;
}

const STATUS_DISPLAY_ORDER: readonly GameTapeStatus[] = [
  GameTapeStatusValue.FAILED,
  GameTapeStatusValue.INTERRUPTED,
  GameTapeStatusValue.ATTENTION_REQUIRED,
  GameTapeStatusValue.RUNNING,
  GameTapeStatusValue.PASSED,
  GameTapeStatusValue.CANCELLED,
  GameTapeStatusValue.SKIPPED,
  GameTapeStatusValue.NOT_RUN,
  GameTapeStatusValue.ADMITTED,
  GameTapeStatusValue.PLANNED,
];

const REVIEW_FOCUS_ORDER: readonly GameTapeStatus[] = [
  GameTapeStatusValue.FAILED,
  GameTapeStatusValue.INTERRUPTED,
  GameTapeStatusValue.ATTENTION_REQUIRED,
  GameTapeStatusValue.RUNNING,
];

export function gameTapeFlowGroups(repository: GameTapeRepositoryExecution): readonly GameTapeFlowGroup[] {
  type MutableFlowGroup = Omit<GameTapeFlowGroup, "steps" | "statusCounts"> & { steps: GameTapeStepExecution[] };
  const groups: MutableFlowGroup[] = [];
  for (const [laneIndex, step] of (repository.steps ?? []).entries()) {
    const source = step.why.source_location?.source ?? null;
    const previous = groups.at(-1);
    if (previous && previous.source === source) {
      previous.steps.push(step);
      continue;
    }
    const identity = flowSourceIdentity(source);
    groups.push({
      key: `${repository.repository_execution_id}:${groups.length}:${source ?? "resolved"}`,
      source,
      ...identity,
      laneStartIndex: laneIndex,
      steps: [step],
    });
  }
  return groups.map((group) => ({
    ...group,
    statusCounts: gameTapeFlowStatusCounts(group.steps),
  }));
}

export function flowSourceIdentity(source: string | null): Pick<GameTapeFlowGroup, "kind" | "displayName"> {
  if (source === null) return { kind: "resolved", displayName: null };
  const normalized = source.replaceAll("\\", "/").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.at(-1) === "goon.yml") {
    const parent = parts.at(-2) ?? null;
    return { kind: "goon", displayName: parent && parent !== "." ? parent : null };
  }
  return { kind: "source", displayName: parts.at(-1) ?? normalized };
}

export function gameTapeFlowSegmentAnchor(repositories: readonly GameTapeRepositoryExecution[], firstStepExecutionId: string | null): GameTapeFlowSegmentAnchor | null {
  if (firstStepExecutionId === null) return null;
  for (const repository of repositories) {
    for (const group of gameTapeFlowGroups(repository)) {
      const firstStep = group.steps[0];
      if (firstStep?.step_execution_id === firstStepExecutionId) {
        return { repositoryExecutionId: repository.repository_execution_id, groupKey: group.key, firstStepExecutionId };
      }
    }
  }
  return null;
}

export function gameTapeFlowGroupStatusCounts(group: GameTapeFlowGroup): readonly GameTapeFlowStatusCount[] {
  return group.statusCounts;
}

export function gameTapeFlowStatusCounts(steps: readonly GameTapeStepExecution[]): readonly GameTapeFlowStatusCount[] {
  const counts = new Map<GameTapeStatus, number>();
  for (const step of steps) counts.set(step.status, (counts.get(step.status) ?? 0) + 1);
  return orderedStatusCounts(counts);
}

function orderedStatusCounts(counts: ReadonlyMap<GameTapeStatus, number>): readonly GameTapeFlowStatusCount[] {
  return STATUS_DISPLAY_ORDER.flatMap((status) => {
    const count = counts.get(status) ?? 0;
    return count > 0 ? [{ status, count }] : [];
  });
}

export function gameTapeFlowRepositoryStatusCounts(repositories: readonly GameTapeRepositoryExecution[]): readonly GameTapeFlowStatusCount[] {
  const counts = new Map<GameTapeStatus, number>();
  for (const repository of repositories) {
    for (const step of repository.steps ?? []) counts.set(step.status, (counts.get(step.status) ?? 0) + 1);
  }
  return orderedStatusCounts(counts);
}

export function gameTapeFlowReviewFocus(repository: GameTapeRepositoryExecution): GameTapeStepExecution | null {
  const steps = repository.steps ?? [];
  for (const status of REVIEW_FOCUS_ORDER) {
    const match = steps.find((step) => step.status === status);
    if (match) return match;
  }
  return null;
}
export function gameTapeFlowStatusMatches(repositories: readonly GameTapeRepositoryExecution[], status: GameTapeStatus): readonly GameTapeFlowSearchMatch[] {
  const matches: GameTapeFlowSearchMatch[] = [];
  for (const repository of repositories) {
    for (const step of repository.steps ?? []) {
      if (step.status === status) matches.push({ repositoryExecutionId: repository.repository_execution_id, stepExecutionId: step.step_execution_id });
    }
  }
  return matches;
}

export function gameTapeFlowSearchMatches(repositories: readonly GameTapeRepositoryExecution[], query: string): readonly GameTapeFlowSearchMatch[] {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) return [];
  const matches: GameTapeFlowSearchMatch[] = [];
  for (const repository of repositories) {
    for (const group of gameTapeFlowGroups(repository)) {
      const groupContext = [group.displayName, group.source].filter(Boolean).join(" ");
      for (const step of group.steps) {
        const haystack = [
          repository.repository.name,
          repository.repository.path,
          groupContext,
          step.step_id,
          step.kind,
        ].join(" ").toLowerCase();
        if (haystack.includes(normalizedQuery)) {
          matches.push({ repositoryExecutionId: repository.repository_execution_id, stepExecutionId: step.step_execution_id });
        }
      }
    }
  }
  return matches;
}
export function gameTapeFlowSelectionContext(repositories: readonly GameTapeRepositoryExecution[], selectedStepExecutionId: string | null): GameTapeFlowSelectionContext | null {
  if (selectedStepExecutionId === null) return null;
  for (const [repositoryIndex, repository] of repositories.entries()) {
    const steps = repository.steps ?? [];
    const laneStepIndex = steps.findIndex((step) => step.step_execution_id === selectedStepExecutionId);
    if (laneStepIndex < 0) continue;
    const step = steps[laneStepIndex];
    if (step === undefined) return null;
    const groups = gameTapeFlowGroups(repository);
    const segmentIndex = groups.findIndex((candidate) => candidate.steps.some((candidateStep) => candidateStep.step_execution_id === selectedStepExecutionId));
    if (segmentIndex < 0) return null;
    const group = groups[segmentIndex];
    if (group === undefined) return null;
    return {
      repository,
      repositoryIndex,
      repositoryCount: repositories.length,
      group,
      step,
      laneStepIndex,
      laneStepCount: steps.length,
      previousStepExecutionId: steps[laneStepIndex - 1]?.step_execution_id ?? null,
      nextStepExecutionId: steps[laneStepIndex + 1]?.step_execution_id ?? null,
      segmentIndex,
      segmentCount: groups.length,
      previousSegmentFirstStepExecutionId: groups[segmentIndex - 1]?.steps[0]?.step_execution_id ?? null,
      nextSegmentFirstStepExecutionId: groups[segmentIndex + 1]?.steps[0]?.step_execution_id ?? null,
    };
  }
  return null;
}

