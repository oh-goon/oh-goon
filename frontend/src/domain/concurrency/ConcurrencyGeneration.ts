export interface GenerationToken<Scope extends string> {
  readonly scope: Scope;
  readonly generation: number;
}

export class ConcurrencyGeneration<Scope extends string> {
  private readonly generations = new Map<Scope, number>();
  private readonly identities = new Map<Scope, string | null>();

  setIdentity(scope: Scope, identity: string | null): boolean {
    const previous = this.identities.get(scope);
    if (previous === identity) return false;
    this.identities.set(scope, identity);
    if (previous === undefined) return false;
    this.invalidate(scope);
    return true;
  }

  invalidate(scope: Scope): number {
    const next = this.current(scope) + 1;
    this.generations.set(scope, next);
    return next;
  }

  current(scope: Scope): number {
    return this.generations.get(scope) ?? 0;
  }

  isCurrent(token: GenerationToken<Scope>): boolean {
    return this.current(token.scope) === token.generation;
  }
}
