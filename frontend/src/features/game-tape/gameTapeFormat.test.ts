import { describe, expect, it } from "vitest";
import { statusSymbol } from "@/features/game-tape/gameTapeFormat";

describe("Game Tape status symbols", () => {
  it("keeps passed, failed, attention and running instantly distinguishable without color", () => {
    expect(statusSymbol("PASSED")).toBe("✓");
    expect(statusSymbol("FAILED")).toBe("×");
    expect(statusSymbol("ATTENTION_REQUIRED")).toBe("!");
    expect(statusSymbol("RUNNING")).toBe("●");
    expect(statusSymbol("NOT_RUN")).toBe("○");
  });
});
