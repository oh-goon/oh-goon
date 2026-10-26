import { CausalityClock, type CausalityRevision } from "@/domain/concurrency/CausalityClock";

export type MutationScope = "fleet-run" | "locations" | "repositories" | "schedules" | "recovery" | "plans" | "league-lineups" | "dependency-selections";
export type MutationDispatch = "exclusive" | "agent-repair" | "fleet-run-cancel";

export interface MutationToken {
  readonly id: number;
  readonly scope: MutationScope;
  readonly dispatch: MutationDispatch;
}

export interface MutationExecution<Value> {
  readonly value: Value;
  readonly causalityRevision: CausalityRevision;
}

type Listener = () => void;
export type MutationCommitListener = (scope: MutationScope, causalityRevision: CausalityRevision) => void;
export type MutationConflictListener = (scope: MutationScope) => void;

export class MutationBusyError extends Error {
  constructor() {
    super("Another mutation is already in progress");
    this.name = "MutationBusyError";
  }
}

export class MutationController {
  private nextId = 0;
  private active: MutationToken | null = null;
  private cancellingRepair: MutationToken | null = null;
  private readonly listeners = new Set<Listener>();
  private readonly commitListeners = new Set<MutationCommitListener>();
  private readonly conflictListeners = new Set<MutationConflictListener>();

  constructor(readonly causality: CausalityClock = new CausalityClock()) {}

  readonly subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  readonly getSnapshot = (): boolean => this.active !== null || this.cancellingRepair !== null;

  readonly subscribeCommits = (listener: MutationCommitListener): (() => void) => {
    this.commitListeners.add(listener);
    return () => this.commitListeners.delete(listener);
  };

  readonly subscribeConflicts = (listener: MutationConflictListener): (() => void) => {
    this.conflictListeners.add(listener);
    return () => this.conflictListeners.delete(listener);
  };

  reportConflict(scope: MutationScope): void {
    for (const listener of this.conflictListeners) {
      try {
        listener(scope);
      } catch {
        // Conflict observers only invalidate stale presentation; they never own command outcome.
      }
    }
  }

  async execute<Result>(
    scope: MutationScope,
    mutation: () => Promise<Result>,
    dispatch: MutationDispatch = "exclusive",
  ): Promise<MutationExecution<Result>> {
    const token = this.acquire(scope, dispatch);
    try {
      const value = await mutation();
      // Once the server confirms a write, UI context cannot make that side effect
      // disappear.  Presentation relevance belongs to consumers; mutation causality
      // always advances for a successfully completed command.
      const causalityRevision = this.causality.advance();
      this.emitCommit(scope, causalityRevision);
      return { value, causalityRevision };
    } finally {
      this.release(token);
    }
  }

  private acquire(scope: MutationScope, dispatch: MutationDispatch): MutationToken {
    // Repair HTTP requests stay open while admitted child Runs execute. Their
    // cancellation must reach the server without releasing the repair's write
    // fence. Every other write, including another cancellation, remains exclusive.
    const canCancelRepair = dispatch === "fleet-run-cancel" && scope === "fleet-run"
      && this.active?.dispatch === "agent-repair" && this.active.scope === "fleet-run"
      && this.cancellingRepair === null;
    if (this.getSnapshot() && !canCancelRepair) throw new MutationBusyError();
    const token = { id: ++this.nextId, scope, dispatch } satisfies MutationToken;
    if (canCancelRepair) this.cancellingRepair = token;
    else this.active = token;
    this.emit();
    return token;
  }

  private release(token: MutationToken): void {
    if (this.active?.id === token.id) this.active = null;
    else if (this.cancellingRepair?.id === token.id) this.cancellingRepair = null;
    else return;
    this.emit();
  }

  private emit(): void {
    this.listeners.forEach((listener) => listener());
  }

  private emitCommit(scope: MutationScope, causalityRevision: CausalityRevision): void {
    // A server-confirmed write is already real. Observer failures must never turn
    // that durable fact into a client-side write failure. Commit observers are
    // therefore advisory invalidation hooks and cannot own mutation outcome.
    for (const listener of this.commitListeners) {
      try {
        listener(scope, causalityRevision);
      } catch {
        // Read invalidation is deliberately best-effort here; consumers still
        // receive the saved result and can refresh explicitly if presentation fails.
      }
    }
  }
}
