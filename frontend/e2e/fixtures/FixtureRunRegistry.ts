import type { FleetPlanTarget, RepositoryIdentity } from "@generated/models";

export type FixtureRunTerminalOutcome = "passed" | "failed";

export interface FixtureRunRecord {
  readonly runId: string;
  readonly targets: readonly RepositoryIdentity[];
  readonly planTarget: FleetPlanTarget;
  readonly terminalOutcome: FixtureRunTerminalOutcome;
  /** 0 means the fixture Run is already terminal; a positive value models live event polling. */
  readonly terminalAfterEventReads: number;
  eventReads: number;
}

export interface RegisterFixtureRunOptions {
  readonly runId: string;
  readonly targets: readonly RepositoryIdentity[];
  readonly planTarget: FleetPlanTarget;
  readonly terminalOutcome?: FixtureRunTerminalOutcome;
  readonly terminalAfterEventReads?: number;
}

/** Canonical browser-fixture truth for Run identity, targets, Plan and lifecycle. */
export class FixtureRunRegistry {
  private readonly records = new Map<string, FixtureRunRecord>();

  register(options: RegisterFixtureRunOptions): FixtureRunRecord {
    if (this.records.has(options.runId)) throw new Error(`Duplicate fixture Run ${options.runId}`);
    if (options.targets.length === 0) throw new Error(`Fixture Run ${options.runId} requires at least one repository`);
    const record: FixtureRunRecord = {
      runId: options.runId,
      targets: [...options.targets],
      planTarget: options.planTarget,
      terminalOutcome: options.terminalOutcome ?? "passed",
      terminalAfterEventReads: options.terminalAfterEventReads ?? 0,
      eventReads: 0,
    };
    this.records.set(options.runId, record);
    return record;
  }

  require(runId: string): FixtureRunRecord {
    const record = this.records.get(runId);
    if (record === undefined) throw new Error(`Unknown fixture Run ${runId}`);
    return record;
  }

  has(runId: string): boolean {
    return this.records.has(runId);
  }

  advanceEventRead(runId: string): FixtureRunRecord {
    const record = this.require(runId);
    record.eventReads += 1;
    return record;
  }

  isTerminal(record: FixtureRunRecord): boolean {
    return record.terminalAfterEventReads === 0 || record.eventReads >= record.terminalAfterEventReads;
  }
}
