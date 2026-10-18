export function lastValidPageOffset(total: number, limit: number): number {
  if (!Number.isInteger(total) || total < 0) throw new Error("Scout total must be a non-negative integer");
  if (!Number.isInteger(limit) || limit <= 0) throw new Error("Scout limit must be a positive integer");
  return total === 0 ? 0 : Math.floor((total - 1) / limit) * limit;
}

