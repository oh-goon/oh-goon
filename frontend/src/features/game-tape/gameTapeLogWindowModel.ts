export const GAME_TAPE_LOG_RENDER_WINDOW = 120;

export type GameTapeLogWindow = Readonly<{
  pageFromEnd: number;
  start: number;
  end: number;
  earlierCount: number;
  laterCount: number;
}>;

export function gameTapeLogWindow(total: number, requestedPageFromEnd: number, pageSize = GAME_TAPE_LOG_RENDER_WINDOW): GameTapeLogWindow {
  const safeTotal = Math.max(0, Math.floor(total));
  const safeSize = Math.max(1, Math.floor(pageSize));
  const maxPageFromEnd = Math.max(0, Math.ceil(safeTotal / safeSize) - 1);
  const pageFromEnd = Math.min(Math.max(0, Math.floor(requestedPageFromEnd)), maxPageFromEnd);
  const end = Math.max(0, safeTotal - pageFromEnd * safeSize);
  const start = Math.max(0, end - safeSize);
  return { pageFromEnd, start, end, earlierCount: start, laterCount: safeTotal - end };
}

export function gameTapeLogPageForIndex(total: number, index: number, pageSize = GAME_TAPE_LOG_RENDER_WINDOW): number | null {
  const safeTotal = Math.max(0, Math.floor(total));
  const safeIndex = Math.floor(index);
  const safeSize = Math.max(1, Math.floor(pageSize));
  if (safeIndex < 0 || safeIndex >= safeTotal) return null;
  return Math.floor((safeTotal - 1 - safeIndex) / safeSize);
}
