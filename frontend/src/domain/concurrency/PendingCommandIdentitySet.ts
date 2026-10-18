import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";

/**
 * Owns only client-generated identities for commands whose commit outcome may be ambiguous.
 * Durable execution identity remains backend-owned once a command is saved.
 */
export interface PendingCommandBinding<Authority> {
  readonly identity: string;
  readonly authority: Authority;
}

interface StoredPendingCommand {
  readonly identity: string;
  readonly authority: unknown;
}

export class PendingCommandIdentitySet {
  private readonly commands = new Map<string, StoredPendingCommand>();

  constructor(private readonly createIdentity: () => string) {}

  forCommand(commandKey: string): string {
    const existing = this.commands.get(commandKey);
    if (existing !== undefined) return existing.identity;
    const created = this.createIdentity();
    this.commands.set(commandKey, { identity: created, authority: undefined });
    return created;
  }

  bindCommand<Authority>(commandKey: string, authority: Authority): PendingCommandBinding<Authority> {
    const existing = this.commands.get(commandKey);
    if (existing !== undefined) {
      return { identity: existing.identity, authority: existing.authority as Authority };
    }
    const identity = this.createIdentity();
    this.commands.set(commandKey, { identity, authority });
    return { identity, authority };
  }

  settle(commandKey: string, result: WriteCommandResult<unknown>): void {
    if (result.outcome === "write-error" && result.recovery !== "blocked") return;
    this.commands.delete(commandKey);
  }

  observeDurableIdentities(identities: Iterable<string>): void {
    const durable = new Set(identities);
    if (durable.size === 0) return;
    for (const [commandKey, command] of this.commands) {
      if (durable.has(command.identity)) this.commands.delete(commandKey);
    }
  }
}
