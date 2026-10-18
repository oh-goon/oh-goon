import { FleetRunOutcome, FleetRunStatus, FleetRunSource } from "@generated/enums";
import { readLocationHash, writeLocationHash } from "@/app/navigation";
import type {
  FleetRunOutcome as FleetRunOutcomeType,
  FleetRunSource as FleetRunSourceType,
  FleetRunStatus as FleetRunStatusType,
} from "@generated/models";
import {
  isGameTapeLogStream,
  isGameTapeView,
  type GameTapeLogStream,
  type GameTapeView,
} from "@/features/game-tape/GameTapeFeature";
import { isRunHistoryFilter, type RunHistoryFilter } from "@/features/history/HistoryFeature";

// History context survives Run selection changes. Run-local review context never does.
type RunHistoryContext = Readonly<{
  historyQuery: string;
  historyFilter: RunHistoryFilter;
  historyOutcome: FleetRunOutcomeType | null;
  historyStatus: FleetRunStatusType | null;
  historySource: FleetRunSourceType | null;
  historyRepositoryId: string | null;
  historyFromAt: string | null;
  historyToAt: string | null;
  historyPlanQuery: string;
}>;

// Every field here is reset when runId changes or history-only mode is entered.
type RunLocalReviewContext = Readonly<{
  runId: string | null;
  stepExecutionId: string | null;
  view: GameTapeView | null;
  baselineRunId: string | null;
  failureFocus: boolean;
  logQuery: string;
  logStream: GameTapeLogStream;
  logPageFromEnd: number;
  rawLogsOpen: boolean;
  technicalOpen: boolean;
  flowQuery: string;
  collapsedLaneIds: readonly string[];
  sourceSegmentStepExecutionId: string | null;
}>;

export type RunReviewState = Readonly<RunHistoryContext & RunLocalReviewContext>;


export type RunReviewCanonicalFacts = Readonly<{
  runId: string;
  stepExecutionIds: ReadonlySet<string>;
  baselineRunIds: ReadonlySet<string> | null;
  repositoryExecutionIds: ReadonlySet<string>;
  sourceSegmentStepExecutionIds: ReadonlySet<string>;
  hasIssues: boolean;
}>;

const EMPTY_RUN_HISTORY_CONTEXT: RunHistoryContext = {
  historyQuery: "",
  historyFilter: "all",
  historyOutcome: null,
  historyStatus: null,
  historySource: null,
  historyRepositoryId: null,
  historyFromAt: null,
  historyToAt: null,
  historyPlanQuery: "",
};

const EMPTY_RUN_LOCAL_REVIEW_CONTEXT: RunLocalReviewContext = {
  runId: null,
  stepExecutionId: null,
  view: null,
  baselineRunId: null,
  failureFocus: false,
  logQuery: "",
  logStream: "all",
  logPageFromEnd: 0,
  rawLogsOpen: false,
  technicalOpen: false,
  flowQuery: "",
  collapsedLaneIds: [],
  sourceSegmentStepExecutionId: null,
};

export const EMPTY_RUN_REVIEW_STATE: RunReviewState = {
  ...EMPTY_RUN_HISTORY_CONTEXT,
  ...EMPTY_RUN_LOCAL_REVIEW_CONTEXT,
};

export function parseRunReviewState(hash: string): RunReviewState {
  const query = hash.split("?", 2)[1];
  const params = new URLSearchParams(query ?? "");
  const view = params.get("view");
  const historyFilter = params.get("history_filter");
  const logStream = params.get("log_stream");
  const historyOutcome = params.get("history_outcome");
  const historyStatus = params.get("history_status");
  const historySource = params.get("history_source");
  const requestedLogPage = Number.parseInt(params.get("log_page") ?? "0", 10);
  return canonicalizeRunPresence({
    runId: params.get("run"),
    stepExecutionId: params.get("step"),
    view: isGameTapeView(view) ? view : null,
    baselineRunId: params.get("baseline"),
    historyQuery: params.get("history_q") ?? "",
    historyFilter: isRunHistoryFilter(historyFilter) ? historyFilter : "all",
    historyOutcome: Object.values(FleetRunOutcome).includes(historyOutcome as FleetRunOutcomeType)
      ? historyOutcome as FleetRunOutcomeType
      : null,
    historyStatus: Object.values(FleetRunStatus).includes(historyStatus as FleetRunStatusType)
      ? historyStatus as FleetRunStatusType
      : null,
    historySource: Object.values(FleetRunSource).includes(historySource as FleetRunSourceType)
      ? historySource as FleetRunSourceType
      : null,
    historyRepositoryId: params.get("repository"),
    historyFromAt: params.get("history_from"),
    historyToAt: params.get("history_to"),
    historyPlanQuery: params.get("history_plan") ?? "",
    failureFocus: params.get("focus") === "failures",
    logQuery: params.get("log_q") ?? "",
    logStream: isGameTapeLogStream(logStream) ? logStream : "all",
    logPageFromEnd: Number.isSafeInteger(requestedLogPage) && requestedLogPage > 0 ? requestedLogPage : 0,
    rawLogsOpen: params.get("raw") === "1",
    technicalOpen: params.get("tech") === "1",
    flowQuery: params.get("flow_q") ?? "",
    collapsedLaneIds: params.getAll("fold").filter((value) => value.trim().length > 0),
    sourceSegmentStepExecutionId: params.get("segment"),
  });
}

export function readRunReviewState(): RunReviewState {
  return parseRunReviewState(readLocationHash());
}

function withoutRunReviewContext(state: RunReviewState, runId: string | null): RunReviewState {
  return {
    ...state,
    ...EMPTY_RUN_LOCAL_REVIEW_CONTEXT,
    runId,
  };
}

function canonicalizeRunPresence(state: RunReviewState): RunReviewState {
  return state.runId === null ? withoutRunReviewContext(state, null) : state;
}

export function patchRunReviewState(
  state: RunReviewState,
  patch: Partial<RunReviewState>,
): RunReviewState {
  const { runId: requestedRunId = state.runId, ...reviewPatch } = patch;
  const base = requestedRunId === state.runId
    ? state
    : withoutRunReviewContext(state, requestedRunId);
  return canonicalizeRunPresence({
    ...base,
    ...reviewPatch,
    runId: requestedRunId,
  });
}

export function normalizeRunReviewStateForLoadedRun(
  state: RunReviewState,
  facts: RunReviewCanonicalFacts,
): RunReviewState {
  if (state.runId !== facts.runId) return state;
  const stepExecutionId = state.stepExecutionId && facts.stepExecutionIds.has(state.stepExecutionId)
    ? state.stepExecutionId
    : null;
  const baselineRunId = facts.baselineRunIds === null
    ? state.baselineRunId
    : state.baselineRunId && facts.baselineRunIds.has(state.baselineRunId)
      ? state.baselineRunId
      : null;
  const collapsedLaneIds: string[] = [];
  const seenLanes = new Set<string>();
  for (const repositoryExecutionId of state.collapsedLaneIds) {
    if (
      !facts.repositoryExecutionIds.has(repositoryExecutionId)
      || seenLanes.has(repositoryExecutionId)
    ) continue;
    seenLanes.add(repositoryExecutionId);
    collapsedLaneIds.push(repositoryExecutionId);
  }
  const sourceSegmentStepExecutionId = state.sourceSegmentStepExecutionId
    && facts.sourceSegmentStepExecutionIds.has(state.sourceSegmentStepExecutionId)
    ? state.sourceSegmentStepExecutionId
    : null;
  return {
    ...state,
    stepExecutionId,
    baselineRunId,
    view: state.view === "diff" && facts.baselineRunIds !== null && facts.baselineRunIds.size === 0 ? "summary" : state.view,
    failureFocus: state.failureFocus && facts.hasIssues,
    logPageFromEnd: stepExecutionId ? state.logPageFromEnd : 0,
    technicalOpen: stepExecutionId ? state.technicalOpen : false,
    collapsedLaneIds,
    sourceSegmentStepExecutionId,
  };
}

export function runReviewHash(state: RunReviewState): string {
  state = canonicalizeRunPresence(state);
  const params = new URLSearchParams();
  if (state.runId) params.set("run", state.runId);
  if (state.stepExecutionId) params.set("step", state.stepExecutionId);
  if (state.view) params.set("view", state.view);
  if (state.baselineRunId) params.set("baseline", state.baselineRunId);
  if (state.historyQuery.trim()) params.set("history_q", state.historyQuery);
  if (state.historyFilter !== "all") params.set("history_filter", state.historyFilter);
  if (state.historyOutcome) params.set("history_outcome", state.historyOutcome);
  if (state.historyStatus) params.set("history_status", state.historyStatus);
  if (state.historySource) params.set("history_source", state.historySource);
  if (state.historyRepositoryId) params.set("repository", state.historyRepositoryId);
  if (state.historyFromAt) params.set("history_from", state.historyFromAt);
  if (state.historyToAt) params.set("history_to", state.historyToAt);
  if (state.historyPlanQuery.trim()) params.set("history_plan", state.historyPlanQuery);
  if (state.failureFocus) params.set("focus", "failures");
  if (state.logQuery.trim()) params.set("log_q", state.logQuery);
  if (state.logStream !== "all") params.set("log_stream", state.logStream);
  if (state.logPageFromEnd > 0) params.set("log_page", String(Math.floor(state.logPageFromEnd)));
  if (state.rawLogsOpen) params.set("raw", "1");
  if (state.technicalOpen) params.set("tech", "1");
  if (state.flowQuery.trim()) params.set("flow_q", state.flowQuery);
  for (const repositoryExecutionId of state.collapsedLaneIds) {
    if (repositoryExecutionId.trim()) params.append("fold", repositoryExecutionId);
  }
  if (state.sourceSegmentStepExecutionId) params.set("segment", state.sourceSegmentStepExecutionId);
  const query = params.toString();
  return query ? `#runs?${query}` : "#runs";
}

export function runReviewUrl(state: RunReviewState, baseHref: string): string {
  const url = new URL(baseHref);
  url.pathname = "/";
  url.search = "";
  url.hash = runReviewHash(state);
  return url.toString();
}

export function writeRunReviewState(mode: "replace" | "push", state: RunReviewState): void {
  writeLocationHash(mode, runReviewHash(state));
}

export function writeRunReviewPatch(mode: "replace" | "push", patch: Partial<RunReviewState>): void {
  writeRunReviewState(mode, patchRunReviewState(readRunReviewState(), patch));
}
