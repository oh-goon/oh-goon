import { useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { useReadResource } from "@/app/reads/useReadResource";
import type { GameTapeLiveOutputBatch, GameTapeLiveOutputChunk } from "@generated/models";
import type { ReadResource } from "@/domain/reads/ReadResource";

interface LiveOutputOwner {
  runId: string;
  after: number;
  accumulated: GameTapeLiveOutputBatch | null;
}

interface LiveOutputRead {
  readonly owner: LiveOutputOwner;
  readonly requestedAfter: number;
  readonly incoming: GameTapeLiveOutputBatch;
}

export function useGameTapeLiveOutput(runId: string, enabled: boolean, polling: boolean): ReadResource<GameTapeLiveOutputBatch> {
  const client = useApiClient();
  const { t } = useTranslation();
  const owner = useRef<LiveOutputOwner>({ runId, after: 0, accumulated: null });
  if (owner.current.runId !== runId) {
    // Reset synchronously with identity so an older async completion cannot write
    // through a one-render effect window into the newly selected Run.
    owner.current = { runId, after: 0, accumulated: null };
  }

  const request = useCallback(async (signal: AbortSignal) => {
    const owned = owner.current.runId === runId
      ? owner.current
      : { runId, after: 0, accumulated: null };
    const requestedAfter = owned.after;
    const incoming = await client.gameTapeLiveOutput(runId, requestedAfter, { signal });
    if (incoming.fleet_run_id !== runId) {
      throw new Error(`Game Tape live output identity mismatch: requested ${runId}, received ${incoming.fleet_run_id}`);
    }
    return { owner: owned, requestedAfter, incoming } satisfies LiveOutputRead;
  }, [client, runId]);
  const accept = useCallback((read: LiveOutputRead) => {
    const { owner: owned, requestedAfter, incoming } = read;
    if (owner.current !== owned) return incoming;
    validateLiveOutputBatch(requestedAfter, owned.accumulated, incoming);
    const merged = mergeLiveOutput(owned.accumulated, incoming);
    owned.accumulated = merged;
    owned.after = incoming.state === "UNAVAILABLE" ? 0 : incoming.next_sequence;
    return merged;
  }, []);
  const { resource } = useReadResource<GameTapeLiveOutputBatch, LiveOutputRead>({
    scope: "game-tape-live-output",
    source: "GET /fleet/runs/:runId/live-output",
    identity: enabled ? runId : null,
    enabled,
    pollIntervalMs: 500,
    shouldPoll: (value) => polling || value.state !== "UNAVAILABLE",
    errorMessage: t("errors.gameTapeLiveOutput"),
    request,
    accept,
  });
  return resource;
}

function mergeLiveOutput(
  current: GameTapeLiveOutputBatch | null,
  incoming: GameTapeLiveOutputBatch,
): GameTapeLiveOutputBatch {
  if (current === null || incoming.state === "UNAVAILABLE") return incoming;
  const retainedFrom = incoming.retained_from_sequence;
  const bySequence = new Map<number, GameTapeLiveOutputChunk>();
  for (const chunk of current.chunks ?? []) {
    if (chunk.sequence >= retainedFrom) bySequence.set(chunk.sequence, chunk);
  }
  for (const chunk of incoming.chunks ?? []) bySequence.set(chunk.sequence, chunk);
  return {
    ...incoming,
    chunks: [...bySequence.values()].sort((left, right) => left.sequence - right.sequence),
    next_sequence: incoming.next_sequence,
    dropped_chunk_count: incoming.dropped_chunk_count,
  };
}


export function validateLiveOutputBatch(
  requestedAfter: number,
  current: GameTapeLiveOutputBatch | null,
  incoming: GameTapeLiveOutputBatch,
): void {
  if (incoming.state === "UNAVAILABLE") return;
  if (incoming.next_sequence < requestedAfter) {
    throw new Error(`Game Tape live output cursor regression: requested after ${requestedAfter}, received next ${incoming.next_sequence}`);
  }
  if (incoming.retained_from_sequence > incoming.next_sequence && incoming.next_sequence !== 0) {
    throw new Error("Game Tape live output retained window exceeds next sequence");
  }
  let previous = requestedAfter;
  for (const chunk of incoming.chunks ?? []) {
    if (chunk.sequence <= requestedAfter) {
      throw new Error(`Game Tape live output replay: requested after ${requestedAfter}, received sequence ${chunk.sequence}`);
    }
    if (chunk.sequence <= previous) {
      throw new Error(`Game Tape live output sequence is not strictly increasing at ${chunk.sequence}`);
    }
    if (chunk.sequence > incoming.next_sequence) {
      throw new Error(`Game Tape live output chunk ${chunk.sequence} exceeds next sequence ${incoming.next_sequence}`);
    }
    previous = chunk.sequence;
  }
  if (current !== null) {
    if (incoming.next_sequence < current.next_sequence) {
      throw new Error(`Game Tape live output next sequence regressed from ${current.next_sequence} to ${incoming.next_sequence}`);
    }
    if (incoming.retained_from_sequence < current.retained_from_sequence) {
      throw new Error(`Game Tape live output retained window regressed from ${current.retained_from_sequence} to ${incoming.retained_from_sequence}`);
    }
    if (incoming.dropped_chunk_count < current.dropped_chunk_count) {
      throw new Error(`Game Tape live output dropped count regressed from ${current.dropped_chunk_count} to ${incoming.dropped_chunk_count}`);
    }
    const currentBySequence = new Map((current.chunks ?? []).map((chunk) => [chunk.sequence, chunk] as const));
    for (const chunk of incoming.chunks ?? []) {
      const prior = currentBySequence.get(chunk.sequence);
      if (prior !== undefined && JSON.stringify(prior) !== JSON.stringify(chunk)) {
        throw new Error(`Game Tape live output sequence ${chunk.sequence} changed after observation`);
      }
    }
  }
}
