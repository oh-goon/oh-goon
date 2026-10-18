import { CausalityClock, type CausalityRevision } from "@/domain/concurrency/CausalityClock";
import { ConcurrencyGeneration } from "@/domain/concurrency/ConcurrencyGeneration";

import type { ReadScope } from "@/domain/reads/readResources";

export interface ReadToken {
  readonly id: number;
  readonly scope: ReadScope;
  readonly generation: number;
  readonly identity: string | null;
  readonly causalityRevision: CausalityRevision;
}

export type ReadExecutionResult<Value> =
  | {
      readonly outcome: "accepted";
      readonly value: Value;
      readonly causalityRevision: CausalityRevision;
    }
  | { readonly outcome: "superseded" };

type Listener = () => void;

interface ActiveRead {
  readonly token: ReadToken;
  readonly controller: AbortController;
}

export type ReadScopeOwner = symbol;

export class ReadController {
  private nextId = 0;
  private readonly active = new Map<ReadScope, ActiveRead>();
  private readonly owners = new Map<ReadScope, ReadScopeOwner>();
  private readonly generations = new ConcurrencyGeneration<ReadScope>();
  private readonly minimumCausality = new Map<ReadScope, CausalityRevision>();
  private readonly listeners = new Map<ReadScope, Set<Listener>>();

  constructor(readonly causality: CausalityClock = new CausalityClock()) {}

  acquireOwner(scope: ReadScope, owner: ReadScopeOwner): void {
    const current = this.owners.get(scope);
    if (current !== undefined && current !== owner) {
      throw new Error(`Read scope already has a mounted owner: ${scope}`);
    }
    this.owners.set(scope, owner);
  }

  releaseOwner(scope: ReadScope, owner: ReadScopeOwner): void {
    if (this.owners.get(scope) === owner) this.owners.delete(scope);
  }

  setIdentity(scope: ReadScope, identity: string | null): void {
    if (this.generations.setIdentity(scope, identity)) this.abortActive(scope);
  }

  invalidate(scope: ReadScope, minimumCausalityRevision?: CausalityRevision): void {
    this.supersede(scope);
    if (minimumCausalityRevision !== undefined) {
      this.minimumCausality.set(
        scope,
        Math.max(this.minimumCausality.get(scope) ?? 0, minimumCausalityRevision),
      );
    }
    this.emit(scope);
  }

  cancel(scope: ReadScope): void {
    this.supersede(scope);
  }

  invalidateMany(
    scopes: readonly ReadScope[],
    minimumCausalityRevision?: CausalityRevision,
  ): void {
    for (const scope of scopes) this.invalidate(scope, minimumCausalityRevision);
  }

  revision(scope: ReadScope): number {
    return this.generations.current(scope);
  }

  subscribe(scope: ReadScope, listener: Listener): () => void {
    const listeners = this.listeners.get(scope) ?? new Set<Listener>();
    listeners.add(listener);
    this.listeners.set(scope, listeners);
    return () => listeners.delete(listener);
  }

  isCurrent(token: ReadToken): boolean {
    const active = this.active.get(token.scope);
    const minimum = this.minimumCausality.get(token.scope) ?? 0;
    return active?.token.id === token.id
      && this.generations.isCurrent(token)
      && token.causalityRevision >= minimum;
  }

  async execute<Value>(
    scope: ReadScope,
    identity: string | null,
    read: (signal: AbortSignal) => Promise<Value>,
  ): Promise<ReadExecutionResult<Value>>;
  async execute<ReadValue, AcceptedValue>(
    scope: ReadScope,
    identity: string | null,
    read: (signal: AbortSignal) => Promise<ReadValue>,
    accept: (value: ReadValue) => AcceptedValue,
  ): Promise<ReadExecutionResult<AcceptedValue>>;
  async execute<ReadValue, AcceptedValue>(
    scope: ReadScope,
    identity: string | null,
    read: (signal: AbortSignal) => Promise<ReadValue>,
    accept?: (value: ReadValue) => AcceptedValue,
  ): Promise<ReadExecutionResult<ReadValue | AcceptedValue>> {
    this.setIdentity(scope, identity);
    const active = this.claim(scope, identity);
    try {
      const value = await read(active.controller.signal);
      if (!this.isCurrent(active.token) || active.controller.signal.aborted) {
        return { outcome: "superseded" };
      }
      return {
        outcome: "accepted",
        value: accept === undefined ? value : accept(value),
        causalityRevision: active.token.causalityRevision,
      };
    } catch (error) {
      if (!this.isCurrent(active.token) || active.controller.signal.aborted) {
        return { outcome: "superseded" };
      }
      throw error;
    } finally {
      this.release(active.token);
    }
  }

  private claim(scope: ReadScope, identity: string | null): ActiveRead {
    this.abortActive(scope);
    const token = {
      id: ++this.nextId,
      scope,
      generation: this.generations.current(scope),
      identity,
      causalityRevision: this.causality.current(),
    } satisfies ReadToken;
    const active = { token, controller: new AbortController() } satisfies ActiveRead;
    this.active.set(scope, active);
    return active;
  }

  private release(token: ReadToken): void {
    if (this.active.get(token.scope)?.token.id === token.id) this.active.delete(token.scope);
  }

  private supersede(scope: ReadScope): void {
    this.generations.invalidate(scope);
    this.abortActive(scope);
  }

  private abortActive(scope: ReadScope): void {
    this.active.get(scope)?.controller.abort();
  }

  private emit(scope: ReadScope): void {
    this.listeners.get(scope)?.forEach((listener) => listener());
  }
}
