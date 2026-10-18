/** Ephemeral UI incarnation for async completions; never backend/runtime authority. */
export interface IdentityIncarnation {
  readonly identity: string;
  readonly generation: number;
}

export function advanceIdentityIncarnation(
  current: IdentityIncarnation,
  identity: string,
): IdentityIncarnation {
  if (current.identity === identity) return current;
  return { identity, generation: current.generation + 1 };
}

export function sameIdentityIncarnation(
  left: IdentityIncarnation,
  right: IdentityIncarnation,
): boolean {
  return left.identity === right.identity && left.generation === right.generation;
}
