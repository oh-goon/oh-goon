import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { GameTapeLiveOutputBatch } from "@generated/models";
import { GAME_TAPE_LOG_RENDER_WINDOW, gameTapeLogPageForIndex, gameTapeLogWindow } from "@/features/game-tape/gameTapeLogWindowModel";
import { canonicalIdentity } from "@/domain/identity/canonicalValue";

export type GameTapeLogStream = "all" | "stdout" | "stderr";
export function isGameTapeLogStream(value: string | null): value is GameTapeLogStream { return value === "all" || value === "stdout" || value === "stderr"; }

export function GameTapeLiveLogs({
  batch,
  repositoryId,
  stepId,
  focusSequence = null,
  query = "",
  stream = "all",
  pageFromEnd = 0,
  onQueryChange,
  onStreamChange,
  onPageChange,
}: {
  batch: GameTapeLiveOutputBatch | null;
  repositoryId: string;
  stepId: string;
  focusSequence?: number | null;
  query?: string;
  stream?: GameTapeLogStream;
  pageFromEnd?: number;
  onQueryChange?: (query: string) => void;
  onStreamChange?: (stream: GameTapeLogStream) => void;
  onPageChange?: (pageFromEnd: number) => void;
}) {
  const { t } = useTranslation();
  const viewport = useRef<HTMLDivElement | null>(null);
  const lastFocused = useRef<number | null>(null);
  const [following, setFollowing] = useState(true);
  const [lastSeenSequence, setLastSeenSequence] = useState(0);
  const renderIdentity = canonicalIdentity({ repositoryId, stepId });
  const currentIdentity = useRef(renderIdentity);
  currentIdentity.current = renderIdentity;
  const [copiedFor, setCopiedFor] = useState<string | null>(null);
  const copied = copiedFor === renderIdentity;
  const [pendingFocus, setPendingFocus] = useState<number | null>(pageFromEnd > 0 ? null : focusSequence);
  const chunks = useMemo(
    () => (batch?.chunks ?? []).filter(
      (chunk) => chunk.repository_id === repositoryId && chunk.step_id === stepId,
    ),
    [batch?.chunks, repositoryId, stepId],
  );
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleChunks = useMemo(
    () => chunks.filter((chunk) => (stream === "all" || chunk.stream === stream) && (!normalizedQuery || chunk.text.toLocaleLowerCase().includes(normalizedQuery))),
    [chunks, normalizedQuery, stream],
  );
  const logWindow = gameTapeLogWindow(visibleChunks.length, pageFromEnd);
  const renderedChunks = visibleChunks.slice(logWindow.start, logWindow.end);
  const latestSequence = visibleChunks.at(-1)?.sequence ?? 0;
  const newerCount = following ? 0 : visibleChunks.filter((chunk) => chunk.sequence > lastSeenSequence).length;


  useEffect(() => {
    setFollowing(true);
    setLastSeenSequence(0);
    setCopiedFor(null);
    setPendingFocus(pageFromEnd > 0 ? null : focusSequence);
    lastFocused.current = null;
  }, [focusSequence, repositoryId, stepId]);

  useEffect(() => {
    if (!following || latestSequence === 0 || focusSequence !== null) return;
    const element = viewport.current;
    if (element) element.scrollTop = element.scrollHeight;
    setLastSeenSequence(latestSequence);
  }, [following, focusSequence, latestSequence]);

  useEffect(() => {
    if (pendingFocus === null || lastFocused.current === pendingFocus) return;
    const focusIndex = visibleChunks.findIndex((chunk) => chunk.sequence === pendingFocus);
    if (focusIndex < 0) return;
    const targetPageFromEnd = gameTapeLogPageForIndex(visibleChunks.length, focusIndex);
    if (targetPageFromEnd !== null && targetPageFromEnd !== logWindow.pageFromEnd) { onPageChange?.(targetPageFromEnd); return; }
    const element = viewport.current?.querySelector<HTMLElement>(`[data-sequence="${pendingFocus}"]`);
    if (!element) return;
    element.scrollIntoView({ block: "center" });
    setFollowing(false);
    setLastSeenSequence(latestSequence);
    lastFocused.current = pendingFocus;
    setPendingFocus(null);
  }, [latestSequence, logWindow.pageFromEnd, pendingFocus, renderedChunks, visibleChunks]);

  useEffect(() => {
    if (!batch?.available || pageFromEnd === logWindow.pageFromEnd) return;
    onPageChange?.(logWindow.pageFromEnd);
  }, [batch?.available, logWindow.pageFromEnd, onPageChange, pageFromEnd]);

  if (!batch?.available) {
    return <section className="game-tape-live-logs">
      <strong>{t("gameTape.logs.liveTitle")}</strong>
      <p className="muted">{t("gameTape.logs.liveUnavailable")}</p>
    </section>;
  }

  return <section className="game-tape-live-logs">
    <header>
      <div>
        <strong>{t("gameTape.logs.liveTitle")}</strong>
        <small>{batch.state === "SEALED" && batch.expires_at
          ? t("gameTape.logs.liveSealed", { time: new Date(batch.expires_at).toLocaleTimeString() })
          : t("gameTape.logs.liveTransient")}</small>
      </div>
      {!following && newerCount > 0 ? <button type="button" onClick={() => setFollowing(true)}>
        {t("gameTape.logs.jumpLatest", { count: newerCount })}
      </button> : null}
    </header>
    {batch.dropped_chunk_count > 0 ? <p className="game-tape-live-logs__gap">{t("gameTape.logs.droppedChunks", { count: batch.dropped_chunk_count })}</p> : null}
    <div className="game-tape-live-logs__filters">
      <label><span>{t("gameTape.logs.search")}</span><input type="search" value={query} onChange={(event) => { const next = event.target.value; setCopiedFor(null); onQueryChange?.(next); }} placeholder={t("gameTape.logs.searchPlaceholder")} /></label>
      <label><span>{t("gameTape.logs.stream")}</span><select value={stream} onChange={(event) => { const next = event.target.value as GameTapeLogStream; setCopiedFor(null); onStreamChange?.(next); }}><option value="all">{t("gameTape.logs.allStreams")}</option><option value="stdout">stdout</option><option value="stderr">stderr</option></select></label>
    </div>
    <div className="game-tape-live-logs__toolbar"><small>{t("gameTape.logs.showingChunks", { visible: renderedChunks.length, total: visibleChunks.length })}</small>{logWindow.earlierCount > 0 ? <button className="button button--secondary button--compact" type="button" onClick={() => { const next = logWindow.pageFromEnd + 1; onPageChange?.(next); setFollowing(false); }}>{t("gameTape.logs.showEarlierChunks", { count: Math.min(GAME_TAPE_LOG_RENDER_WINDOW, logWindow.earlierCount) })}</button> : null}{logWindow.laterCount > 0 ? <button className="button button--secondary button--compact" type="button" onClick={() => { const next = Math.max(0, logWindow.pageFromEnd - 1); onPageChange?.(next); }}>{t("gameTape.logs.showNewerChunks", { count: Math.min(GAME_TAPE_LOG_RENDER_WINDOW, logWindow.laterCount) })}</button> : null}{focusSequence !== null ? <button className="button button--secondary button--compact" type="button" onClick={() => { onQueryChange?.(""); onStreamChange?.("all"); lastFocused.current = null; setPendingFocus(focusSequence); }}>{t("gameTape.logs.jumpFailure")}</button> : null}<button className="button button--secondary button--compact" type="button" disabled={renderedChunks.length === 0} onClick={() => { const ownerIdentity = renderIdentity; const text = renderedChunks.map((chunk) => `${chunk.attempt}:${chunk.stream}:${chunk.line_start}${chunk.line_end !== chunk.line_start ? `-${chunk.line_end}` : ""} ${chunk.text}`).join("\n"); void navigator.clipboard.writeText(text).then(() => { if (currentIdentity.current === ownerIdentity) setCopiedFor(ownerIdentity); }, () => { if (currentIdentity.current === ownerIdentity) setCopiedFor(null); }); }}>{copied ? t("gameTape.logs.copiedVisible") : t("gameTape.logs.copyVisible")}</button></div>
    <small>{t("gameTape.logs.boundedWindow", { count: GAME_TAPE_LOG_RENDER_WINDOW })}</small>
    <div
      className="game-tape-live-logs__viewport"
      ref={viewport}
      onScroll={(event) => {
        const element = event.currentTarget;
        const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 20;
        if (atBottom) {
          setFollowing(true);
          setLastSeenSequence(latestSequence);
        } else if (following) {
          setFollowing(false);
          setLastSeenSequence(latestSequence);
        }
      }}
    >
      {renderedChunks.length > 0 ? <pre>{renderedChunks.map((chunk) => (
        <span
          className={focusSequence === chunk.sequence ? "game-tape-live-line game-tape-live-line--anchor" : "game-tape-live-line"}
          key={chunk.sequence}
          data-sequence={chunk.sequence}
          data-stream={chunk.stream}
        >
          <b>{chunk.attempt}:{chunk.stream}:{chunk.line_start}{chunk.line_end !== chunk.line_start ? `-${chunk.line_end}` : ""}</b>{" "}{chunk.text}
        </span>
      ))}</pre> : <p className="muted">{chunks.length > 0 ? t("gameTape.logs.noMatches") : t("gameTape.logs.liveEmpty")}</p>}
    </div>
    {!following ? <small>{t("gameTape.logs.viewingEarlier", { count: newerCount })}</small> : null}
  </section>;
}
