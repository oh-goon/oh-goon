import type { CausalityRevision } from "@/domain/concurrency/CausalityClock";

export type ReadResourceStatus = "read-idle" | "read-loading" | "read-refreshing" | "read-fresh" | "read-stale" | "read-error";

export interface ReadFailure {
  readonly diagnosticId: string;
  readonly message: string;
  readonly recoverable: boolean;
}

interface ReadResourceBase<Value> {
  readonly data: Value | null;
  readonly revision: string | null;
  readonly causalityRevision: CausalityRevision | null;
  readonly fetchedAt: string | null;
  readonly failure: ReadFailure | null;
}

export type ReadResource<Value> =
  | (ReadResourceBase<Value> & { readonly status: "read-idle"; readonly data: null; readonly revision: null; readonly causalityRevision: null; readonly fetchedAt: null; readonly failure: null })
  | (ReadResourceBase<Value> & { readonly status: "read-loading"; readonly data: null; readonly revision: null; readonly causalityRevision: null; readonly fetchedAt: null; readonly failure: null })
  | (ReadResourceBase<Value> & { readonly status: "read-refreshing"; readonly data: Value; readonly revision: string; readonly causalityRevision: CausalityRevision; readonly fetchedAt: string; readonly failure: null })
  | (ReadResourceBase<Value> & { readonly status: "read-fresh"; readonly data: Value; readonly revision: string; readonly causalityRevision: CausalityRevision; readonly fetchedAt: string; readonly failure: null })
  | (ReadResourceBase<Value> & { readonly status: "read-stale"; readonly data: Value; readonly revision: string; readonly causalityRevision: CausalityRevision; readonly fetchedAt: string; readonly failure: ReadFailure })
  | (ReadResourceBase<Value> & { readonly status: "read-error"; readonly data: null; readonly revision: null; readonly causalityRevision: null; readonly fetchedAt: null; readonly failure: ReadFailure });

export function idleReadResource<Value>(): ReadResource<Value> {
  return {
    status: "read-idle",
    data: null,
    revision: null,
    causalityRevision: null,
    fetchedAt: null,
    failure: null,
  };
}

export function beginRead<Value>(current: ReadResource<Value>): ReadResource<Value> {
  if (current.status === "read-fresh" || current.status === "read-stale" || current.status === "read-refreshing") {
    return {
      status: "read-refreshing",
      data: current.data,
      revision: current.revision,
      causalityRevision: current.causalityRevision,
      fetchedAt: current.fetchedAt,
      failure: null,
    };
  }
  return {
    status: "read-loading",
    data: null,
    revision: null,
    causalityRevision: null,
    fetchedAt: null,
    failure: null,
  };
}

export function completeRead<Value>(
  value: Value,
  revision: string,
  causalityRevision: CausalityRevision,
  fetchedAt: string,
): ReadResource<Value> {
  return {
    status: "read-fresh",
    data: value,
    revision,
    causalityRevision,
    fetchedAt,
    failure: null,
  };
}

export function failRead<Value>(current: ReadResource<Value>, failure: ReadFailure): ReadResource<Value> {
  if (
    current.data !== null
    && current.revision !== null
    && current.causalityRevision !== null
    && current.fetchedAt !== null
  ) {
    return {
      status: "read-stale",
      data: current.data,
      revision: current.revision,
      causalityRevision: current.causalityRevision,
      fetchedAt: current.fetchedAt,
      failure,
    };
  }
  return {
    status: "read-error",
    data: null,
    revision: null,
    causalityRevision: null,
    fetchedAt: null,
    failure,
  };
}
