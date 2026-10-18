import { CausalityClock, type CausalityRevision } from "@/domain/concurrency/CausalityClock";

export type MutationScope = "fleet-run" | "locations" | "repositories" | "schedules" | "recovery" | "plans" | "league-lineups" | "dependency-selections";

export interface MutationToken {
  readonly id: number;
  readonly scope: MutationScope;
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
  private readonly listeners = new Set<Listener>();
  private readonly commitListeners = new Set<MutationCommitListener>();
  private readonly conflictListeners = new Set<MutationConflictListener>();

  constructor(readonly causality: CausalityClock = new CausalityClock()) {}

  readonly subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  readonly getSnapshot = (): boolean => this.active !== null;

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
  ): Promise<MutationExecution<Result>> {
    const token = this.acquire(scope);
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

  private acquire(scope: MutationScope): MutationToken {
    if (this.active !== null) throw new MutationBusyError();
    const token = { id: ++this.nextId, scope } satisfies MutationToken;
    this.active = token;
    this.emit();
    return token;
  }

  private release(token: MutationToken): void {
    if (this.active?.id !== token.id) return;
    this.active = null;
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
