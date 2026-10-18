import { describe, expect, it } from "vitest";

import { destinationAvailable, editorWriteAuthority, templateReplaySafe } from "@/features/league/writeAuthority";

describe("League write authority", () => {
  it("rejects fresh-name collisions but permits the currently owned destination", () => {
    expect(destinationAvailable("new", "current", ["current", "taken"])).toBe(true);
    expect(destinationAvailable("taken", "current", ["current", "taken"])).toBe(false);
    expect(destinationAvailable("current", "current", ["current", "taken"])).toBe(true);
  });

  it("requires fresh catalog/owned reads and rejects stale editors", () => {
    const base = { targetKey: "current", ownedKey: "current", existingKeys: ["current"] } as const;
    expect(editorWriteAuthority({ ...base, catalogFresh: true, targetReadFresh: true })).toBe(true);
    expect(editorWriteAuthority({ ...base, catalogFresh: false, targetReadFresh: true })).toBe(false);
    expect(editorWriteAuthority({ ...base, catalogFresh: true, targetReadFresh: false })).toBe(false);
    expect(editorWriteAuthority({ ...base, catalogFresh: true, targetReadFresh: true, editorStale: true })).toBe(false);
  });

  it("allows template replay only for the exact reviewed SHA incarnation", () => {
    expect(templateReplaySafe({ sha256: "a" }, undefined)).toBe(true);
    expect(templateReplaySafe({ sha256: "a" }, { sha256: "a" })).toBe(true);
    expect(templateReplaySafe({ sha256: "a" }, { sha256: "b" })).toBe(false);
    expect(templateReplaySafe(undefined, undefined)).toBe(false);
  });
});
