import { canonicalIdentity } from "@/domain/identity/canonicalValue";

describe("canonicalIdentity", () => {
  it("does not depend on object insertion order", () => {
    expect(canonicalIdentity({ python: "3.12", threshold: "85" })).toBe(
      canonicalIdentity({ threshold: "85", python: "3.12" }),
    );
  });

  it("preserves array order while canonicalizing nested objects", () => {
    expect(canonicalIdentity({ targets: [{ id: "a", value: 1 }, { value: 2, id: "b" }] })).toBe(
      canonicalIdentity({ targets: [{ value: 1, id: "a" }, { id: "b", value: 2 }] }),
    );
    expect(canonicalIdentity({ targets: ["a", "b"] })).not.toBe(canonicalIdentity({ targets: ["b", "a"] }));
  });

  it("keeps opaque values distinct without separator parsing", () => {
    expect(canonicalIdentity({ left: "a:b", right: "c" })).not.toBe(
      canonicalIdentity({ left: "a", right: "b:c" }),
    );
  });
});

it("fails closed for values whose representation is not plain JSON-like data", () => {
  expect(() => canonicalIdentity(new Date("2026-08-27T00:00:00Z"))).toThrow(/plain objects/);
  expect(() => canonicalIdentity(Number.NaN)).toThrow(/finite/);
  expect(() => canonicalIdentity(Number.POSITIVE_INFINITY)).toThrow(/finite/);
});

it("does not collapse sparse arrays into dense arrays", () => {
  const sparse = new Array<string | undefined>(1);
  expect(canonicalIdentity(sparse)).not.toBe(canonicalIdentity([undefined]));
  expect(canonicalIdentity(sparse)).not.toBe(canonicalIdentity([]));
});

it("fails closed for cyclic identity values", () => {
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;
  expect(() => canonicalIdentity(cyclic)).toThrow(/cycles/);
});

it("rejects hidden representation outside plain enumerable data", () => {
  const symbolKeyed: Record<PropertyKey, unknown> = { visible: "ok" };
  symbolKeyed[Symbol("hidden")] = "value";
  expect(() => canonicalIdentity(symbolKeyed)).toThrow(/keys must be strings/);

  const accessor: Record<string, unknown> = {};
  Object.defineProperty(accessor, "value", { enumerable: true, get: () => "derived" });
  expect(() => canonicalIdentity(accessor)).toThrow(/enumerable data properties/);

  const extendedArray = ["a"] as string[] & { label?: string };
  extendedArray.label = "hidden-semantic-state";
  expect(() => canonicalIdentity(extendedArray)).toThrow(/extra properties/);
});

