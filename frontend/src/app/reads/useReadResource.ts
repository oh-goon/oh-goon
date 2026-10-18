import { useCallback, useEffect, useRef, useState } from "react";

import { useReadController } from "@/app/ApiClientProvider";
import { recordReadFailure } from "@/app/reads/recordReadFailure";
import type { ReadScope } from "@/domain/reads/readResources";
import {
  beginRead,
  completeRead,
  failRead,
  idleReadResource,
  type ReadResource,
} from "@/domain/reads/ReadResource";
import { contentRevision } from "@/domain/reads/readRevision";

interface ReadResourceBaseOptions<Value> {
  readonly scope: ReadScope;
  readonly source: string;
  readonly identity?: string | null;
  readonly enabled?: boolean;
  readonly auto?: boolean;
  readonly debounceMs?: number;
  readonly pollIntervalMs?: number;
  readonly shouldPoll?: (value: Value) => boolean;
  readonly errorMessage: string;
  /** Fail closed when a structurally valid payload does not belong to this request identity. */
  readonly validate?: (value: Value) => void;
  readonly revision?: (value: Value) => string;
  /** Seed an already-read canonical value without immediately reading it a second time. */
  readonly initialValue?: Value;
  /** Decide whether confirmed data remains displayable after this exact read fails. */
  readonly retainDataOnError?: (error: unknown) => boolean;
}

export type ReadResourceOptions<Value, ReadValue = Value> = ReadResourceBaseOptions<Value> & (
  | {
      readonly request: (signal: AbortSignal) => Promise<Value>;
      readonly accept?: undefined;
    }
  | {
      readonly request: (signal: AbortSignal) => Promise<ReadValue>;
      /** Project request-local state only after ReadController accepts this exact read generation. */
      readonly accept: (value: ReadValue) => Value;
    }
);

export interface ReadResourceHandle<Value> {
  readonly resource: ReadResource<Value>;
  refresh(): Promise<Value | null>;
  clear(): void;
}

export function useReadResource<Value, ReadValue = Value>(options: ReadResourceOptions<Value, ReadValue>): ReadResourceHandle<Value> {
  const reads = useReadController();
  const ownerRef = useRef<symbol | null>(null);
  if (ownerRef.current === null) ownerRef.current = Symbol(options.scope);
  const owner = ownerRef.current;
  const enabled = options.enabled ?? true;
  const auto = options.auto ?? true;
  const validateRef = useRef(options.validate);
  validateRef.current = options.validate;
  const shouldPollRef = useRef(options.shouldPoll);
  shouldPollRef.current = options.shouldPoll;
  const pollRefreshInFlightRef = useRef(false);
  const identity = options.identity ?? null;
  const initialSeed = useRef<{ identity: string | null; invalidationRevision: number; present: boolean }>({
    identity,
    invalidationRevision: reads.revision(options.scope),
    present: options.initialValue !== undefined,
  });
  const [storedResource, setStoredResource] = useState<ReadResource<Value>>(() => {
    if (options.initialValue === undefined) return idleReadResource<Value>();
    const revision = (options.revision ?? contentRevision)(options.initialValue);
    return completeRead(
      options.initialValue,
      revision,
      reads.causality.current(),
      new Date().toISOString(),
    );
  });
  const [invalidationRevision, setInvalidationRevision] = useState(() => reads.revision(options.scope));
  const previousIdentity = useRef(identity);
  const resource = previousIdentity.current === identity ? storedResource : idleReadResource<Value>();

  useEffect(() => reads.subscribe(options.scope, () => setInvalidationRevision(reads.revision(options.scope))), [options.scope, reads]);
  useEffect(() => {
    reads.acquireOwner(options.scope, owner);
    const identityChanged = previousIdentity.current !== identity;
    previousIdentity.current = identity;
    reads.setIdentity(options.scope, identity);
    if (identityChanged) setStoredResource(idleReadResource<Value>());
    return () => {
      reads.cancel(options.scope);
      reads.releaseOwner(options.scope, owner);
    };
  }, [identity, options.scope, owner, reads]);
  useEffect(() => {
    if (!enabled) reads.cancel(options.scope);
  }, [enabled, options.scope, reads]);

  const refresh = useCallback(async () => {
    if (!enabled) return null;
    setStoredResource((current) => beginRead(current));
    try {
      const execution = options.accept === undefined
        ? await reads.execute(options.scope, identity, options.request)
        : await reads.execute(options.scope, identity, options.request, options.accept);
      if (execution.outcome === "superseded") return null;
      const value = execution.value;
      validateRef.current?.(value);
      const revision = (options.revision ?? contentRevision)(value);
      setStoredResource(completeRead(value, revision, execution.causalityRevision, new Date().toISOString()));
      return value;
    } catch (error) {
      const failure = recordReadFailure(
        options.source,
        error,
        options.errorMessage,
        options.scope,
        reads.causality.current(),
      );
      const retainData = options.retainDataOnError?.(error) ?? true;
      setStoredResource((current) => retainData ? failRead(current, failure) : failRead(idleReadResource<Value>(), failure));
      return null;
    }
  }, [enabled, identity, options.accept, options.errorMessage, options.request, options.retainDataOnError, options.revision, options.scope, options.source, reads]);

  useEffect(() => {
    if (!enabled || !auto) return;
    const seed = initialSeed.current;
    if (seed.present && seed.identity === identity && seed.invalidationRevision === invalidationRevision) return;
    const debounceMs = options.debounceMs ?? 0;
    if (debounceMs <= 0) {
      void refresh();
      return;
    }
    const timer = window.setTimeout(() => void refresh(), debounceMs);
    return () => window.clearTimeout(timer);
  }, [auto, enabled, invalidationRevision, options.debounceMs, refresh]);

  useEffect(() => {
    const interval = options.pollIntervalMs;
    if (!enabled || interval === undefined) return;
    if (resource.status === "read-loading" || resource.status === "read-refreshing") return;
    if (resource.data === null && resource.status !== "read-error") return;
    if (resource.status === "read-error" && resource.failure?.recoverable === false) return;
    if (resource.data !== null && shouldPollRef.current !== undefined && !shouldPollRef.current(resource.data)) return;

    const pollRefresh = async () => {
      if (document.visibilityState !== "visible" || pollRefreshInFlightRef.current) return;
      pollRefreshInFlightRef.current = true;
      try {
        await refresh();
      } finally {
        pollRefreshInFlightRef.current = false;
      }
    };
    const overdue = () => {
      const fetchedAt = resource.fetchedAt === null ? 0 : Date.parse(resource.fetchedAt);
      return !Number.isFinite(fetchedAt) || Date.now() - fetchedAt >= interval;
    };
    let timer = window.setTimeout(() => void pollRefresh(), interval);
    const resumeIfOverdue = () => {
      if (document.visibilityState !== "visible" || !overdue()) return;
      window.clearTimeout(timer);
      void pollRefresh();
      timer = window.setTimeout(() => void pollRefresh(), interval);
    };
    const onVisibilityChange = () => resumeIfOverdue();
    const onOnline = () => resumeIfOverdue();
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("online", onOnline);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("online", onOnline);
    };
  }, [
    enabled,
    options.pollIntervalMs,
    refresh,
    resource.data,
    resource.failure?.diagnosticId,
    resource.fetchedAt,
    resource.status,
  ]);

  useEffect(() => {
    if (!enabled || !auto || options.pollIntervalMs !== undefined) return;
    if (resource.status !== "read-error" && resource.status !== "read-stale") return;
    const resume = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const onVisibilityChange = () => resume();
    const onOnline = () => resume();
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("online", onOnline);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("online", onOnline);
    };
  }, [auto, enabled, options.pollIntervalMs, refresh, resource.status]);

  const clear = useCallback(() => {
    reads.invalidate(options.scope);
    setStoredResource(idleReadResource<Value>());
  }, [options.scope, reads]);

  return { resource, refresh, clear };
}
