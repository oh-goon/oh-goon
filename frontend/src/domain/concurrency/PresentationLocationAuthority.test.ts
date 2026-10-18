import { describe, expect, it } from "vitest";

import { PresentationLocationAuthority } from "@/domain/concurrency/PresentationLocationAuthority";

describe("PresentationLocationAuthority", () => {
  it("rejects an async owner after A -> B -> A navigation ABA", () => {
    const authority = new PresentationLocationAuthority("#runs?run=a");
    const firstA = authority.capture();

    authority.observe("#runs?run=b");
    authority.observe("#runs?run=a");

    expect(authority.owns(firstA)).toBe(false);
    expect(authority.owns(authority.capture())).toBe(true);
  });

  it("does not advance generation for duplicate observation of the same location", () => {
    const authority = new PresentationLocationAuthority("#runs?run=a");
    const owner = authority.capture();
    expect(authority.observe("#runs?run=a")).toBe(false);
    expect(authority.owns(owner)).toBe(true);
  });
});
