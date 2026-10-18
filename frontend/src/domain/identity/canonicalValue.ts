/**
 * Deterministic serialization for structured local identities.
 *
 * Domain identifiers remain opaque strings. When several values together form
 * a read/mutation identity, serialize the structured value rather than relying
 * on separators or JavaScript object insertion order.
 *
 * This boundary is intentionally fail-closed: identity values must be plain
 * JSON-like data (plus explicit `undefined`). Class instances, functions,
 * symbols, bigint values, cycles, and non-finite numbers are rejected instead
 * of silently collapsing to the same representation.
 */
export function canonicalSerialize(value: unknown): string {
  return serializeCanonical(value, new Set<object>());
}

function serializeCanonical(value: unknown, ancestors: Set<object>): string {
  if (value === null) return "null";
  if (value === undefined) return "undefined";

  switch (typeof value) {
    case "string":
    case "boolean":
      return JSON.stringify(value);
    case "number":
      if (!Number.isFinite(value)) throw new TypeError("Canonical identity numbers must be finite");
      return JSON.stringify(value);
    case "bigint":
    case "function":
    case "symbol":
      throw new TypeError(`Unsupported canonical identity value: ${typeof value}`);
    case "object":
      break;
  }

  if (ancestors.has(value)) throw new TypeError("Canonical identity values must not contain cycles");
  ancestors.add(value);
  try {
    if (Array.isArray(value)) {
      const ownKeys = Reflect.ownKeys(value);
      const unexpectedKey = ownKeys.find((key) =>
        typeof key !== "string" || (key !== "length" && !isArrayIndexKey(key, value.length)),
      );
      if (unexpectedKey !== undefined) {
        throw new TypeError("Canonical identity arrays must not have extra properties");
      }
      const items = Array.from({ length: value.length }, (_, index) =>
        index in value ? serializeCanonical(value[index], ancestors) : "<hole>",
      );
      return `[${items.join(",")}]`;
    }

    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) {
      throw new TypeError("Canonical identity objects must be plain objects");
    }
    const ownKeys = Reflect.ownKeys(value);
    if (ownKeys.some((key) => typeof key !== "string")) {
      throw new TypeError("Canonical identity object keys must be strings");
    }
    const record = value as Record<string, unknown>;
    const entries = (ownKeys as string[])
      .sort()
      .map((key) => {
        const descriptor = Object.getOwnPropertyDescriptor(value, key);
        if (!descriptor?.enumerable || !("value" in descriptor)) {
          throw new TypeError("Canonical identity objects must contain enumerable data properties only");
        }
        return `${JSON.stringify(key)}:${serializeCanonical(record[key], ancestors)}`;
      });
    return `{${entries.join(",")}}`;
  } finally {
    ancestors.delete(value);
  }
}

function isArrayIndexKey(key: string, length: number): boolean {
  if (!/^(0|[1-9]\d*)$/.test(key)) return false;
  const index = Number(key);
  return Number.isSafeInteger(index) && index >= 0 && index < length && String(index) === key;
}

export function canonicalIdentity(value: unknown): string {
  return canonicalSerialize(value);
}
