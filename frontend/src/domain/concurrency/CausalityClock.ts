export type CausalityRevision = number;

export class CausalityClock {
  private revision = 0;

  current(): CausalityRevision {
    return this.revision;
  }

  advance(): CausalityRevision {
    this.revision += 1;
    return this.revision;
  }

  observe(revision: CausalityRevision): CausalityRevision {
    if (revision > this.revision) this.revision = revision;
    return this.revision;
  }
}
