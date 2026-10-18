import { describe, expect, it } from "vitest";

import { gameTapeArtifactKey } from "@/features/game-tape/gameTapeArtifacts";

describe("Game Tape artifact identity", () => {
  it("keeps repository identity in the step-artifact key", () => {
    expect(gameTapeArtifactKey("repo-a", "python-quality.pytest")).not.toBe(gameTapeArtifactKey("repo-b", "python-quality.pytest"));
  });
});
