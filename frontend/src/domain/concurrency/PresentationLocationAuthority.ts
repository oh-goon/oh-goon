export interface PresentationLocationOwner {
  readonly location: string;
  readonly generation: number;
}

/**
 * Ephemeral presentation ownership for async completions.
 *
 * Location text alone is ABA-unsafe: #a -> #b -> #a would let an async result
 * from the first #a attach to the later #a view. Each observed navigation
 * transition therefore advances a generation while keeping runtime truth in
 * the backend/read-mutation authorities.
 */
export class PresentationLocationAuthority {
  private location: string;
  private generation = 0;

  constructor(initialLocation: string) {
    this.location = initialLocation;
  }

  observe(location: string): boolean {
    if (location === this.location) return false;
    this.location = location;
    this.generation += 1;
    return true;
  }

  capture(): PresentationLocationOwner {
    return { location: this.location, generation: this.generation };
  }

  owns(owner: PresentationLocationOwner): boolean {
    return owner.location === this.location && owner.generation === this.generation;
  }
}
