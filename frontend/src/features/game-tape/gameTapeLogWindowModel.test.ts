import { describe, expect, it } from "vitest";

import { GAME_TAPE_LOG_RENDER_WINDOW, gameTapeLogPageForIndex, gameTapeLogWindow } from "@/features/game-tape/gameTapeLogWindowModel";

describe("Game Tape bounded log-window model", () => {
  it("renders at most 120 chunks while keeping the newest window visible by default", () => {
    const window = gameTapeLogWindow(2_000, 0);
    expect(GAME_TAPE_LOG_RENDER_WINDOW).toBe(120);
    expect(window).toEqual({ pageFromEnd: 0, start: 1_880, end: 2_000, earlierCount: 1_880, laterCount: 0 });
    expect(window.end - window.start).toBeLessThanOrEqual(120);
  });

  it("walks older windows without losing a loaded chunk", () => {
    const ranges = Array.from({ length: Math.ceil(2_000 / 120) }, (_, page) => gameTapeLogWindow(2_000, page));
    const covered = new Set<number>();
    for (const range of ranges) for (let index = range.start; index < range.end; index += 1) covered.add(index);
    expect(covered.size).toBe(2_000);
    expect(ranges.at(-1)).toEqual({ pageFromEnd: 16, start: 0, end: 80, earlierCount: 0, laterCount: 1_920 });
  });

  it("opens the bounded page containing a canonical failure sequence", () => {
    expect(gameTapeLogPageForIndex(2_000, 1_999)).toBe(0);
    expect(gameTapeLogPageForIndex(2_000, 1_879)).toBe(1);
    expect(gameTapeLogPageForIndex(2_000, 17)).toBe(16);
    expect(gameTapeLogPageForIndex(2_000, -1)).toBeNull();
  });
});
