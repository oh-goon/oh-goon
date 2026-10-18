import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { createApiClient } from "@/app/createApiClient";
import type { ApiClient } from "@/domain/api/ApiClient";
import { CausalityClock } from "@/domain/concurrency/CausalityClock";
import { PresentationLocationAuthority, type PresentationLocationOwner } from "@/domain/concurrency/PresentationLocationAuthority";
import { MutationController, type MutationScope } from "@/domain/concurrency/MutationController";
import { ReadController } from "@/domain/concurrency/ReadController";
import type { WriteCommandResult } from "@/domain/mutations/WriteCommandResult";
import { readScopesInvalidatedByMutation } from "@/domain/reads/readResources";
import { readLocationHash } from "@/app/navigation";

interface ApiContextValue {
  client: ApiClient;
  mutations: MutationController;
  reads: ReadController;
  mutationFeedback: WriteCommandResult<unknown> | null;
  captureMutationFeedbackOwner: () => PresentationLocationOwner;
  ownsMutationFeedbackOwner: (owner: PresentationLocationOwner) => boolean;
  reportMutationFeedback: (scope: MutationScope, owner: PresentationLocationOwner, result: WriteCommandResult<unknown>) => void;
  clearMutationFeedback: () => void;
}

interface ApiClientProviderProps {
  children: ReactNode;
  client?: ApiClient;
  mutationController?: MutationController;
  readController?: ReadController;
}

const ApiContext = createContext<ApiContextValue | null>(null);

export function ApiClientProvider({ children, client: suppliedClient, mutationController, readController }: ApiClientProviderProps) {
  const [mutationFeedback, setMutationFeedback] = useState<WriteCommandResult<unknown> | null>(null);
  const feedbackAuthority = useMemo(() => new PresentationLocationAuthority(readLocationHash()), []);
  const mountAuthority = useRef({ suppliedClient, mutationController, readController });
  if (mountAuthority.current.suppliedClient !== suppliedClient
      || mountAuthority.current.mutationController !== mutationController
      || mountAuthority.current.readController !== readController) {
    throw new Error("ApiClientProvider client/controller identities are mount-bound; remount the provider to replace transport authority");
  }
  const [transport] = useState(() => {
    const causality = mutationController?.causality ?? readController?.causality ?? new CausalityClock();
    if (mutationController !== undefined && readController !== undefined && mutationController.causality !== readController.causality) {
      throw new Error("ApiClientProvider requires read and mutation controllers to share one causality clock");
    }
    const mutations = mutationController ?? new MutationController(causality);
    const reads = readController ?? new ReadController(causality);
    return { client: suppliedClient ?? createApiClient(mutations), mutations, reads };
  });
  useEffect(() => {
    const observeLocation = () => {
      if (feedbackAuthority.observe(readLocationHash())) setMutationFeedback(null);
    };
    window.addEventListener("hashchange", observeLocation);
    window.addEventListener("popstate", observeLocation);
    return () => {
      window.removeEventListener("hashchange", observeLocation);
      window.removeEventListener("popstate", observeLocation);
    };
  }, [feedbackAuthority]);
  useEffect(() => transport.mutations.subscribeCommits((scope, causalityRevision) => {
    // The mutation boundary owns durable-write invalidation. Feature hooks may
    // validate or reject response ownership, but that must never hide a server
    // commit from dependent reads.
    transport.reads.invalidateMany(readScopesInvalidatedByMutation(scope), causalityRevision);
  }), [transport.mutations, transport.reads]);
  useEffect(() => transport.mutations.subscribeConflicts((scope) => {
    // A server conflict proves the presentation cut that authorized the command is stale.
    // No causality revision advances because the rejected write did not commit.
    transport.reads.invalidateMany(readScopesInvalidatedByMutation(scope));
  }), [transport.mutations, transport.reads]);

  const captureMutationFeedbackOwner = useCallback(() => feedbackAuthority.capture(), [feedbackAuthority]);
  const ownsMutationFeedbackOwner = useCallback((owner: PresentationLocationOwner) => feedbackAuthority.owns(owner), [feedbackAuthority]);
  const reportMutationFeedback = useCallback((_scope: MutationScope, owner: PresentationLocationOwner, result: WriteCommandResult<unknown>) => {
    // A command remains causally real after navigation, but presentation
    // feedback belongs only to the exact navigation incarnation that initiated it.
    if (feedbackAuthority.owns(owner)) setMutationFeedback(result);
  }, [feedbackAuthority]);
  const clearMutationFeedback = useCallback(() => setMutationFeedback(null), []);
  const value = useMemo<ApiContextValue>(() => ({
    ...transport,
    mutationFeedback,
    captureMutationFeedbackOwner,
    ownsMutationFeedbackOwner,
    reportMutationFeedback,
    clearMutationFeedback,
  }), [captureMutationFeedbackOwner, clearMutationFeedback, mutationFeedback, ownsMutationFeedbackOwner, reportMutationFeedback, transport]);
  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

export function useApiClient(): ApiClient {
  return useApiContext().client;
}

export function useReadController(): ReadController {
  return useApiContext().reads;
}

export function useMutationBusy(): boolean {
  const mutations = useApiContext().mutations;
  return useSyncExternalStore(mutations.subscribe, mutations.getSnapshot, mutations.getSnapshot);
}

export function useReportMutationFeedback(scope: MutationScope): (result: WriteCommandResult<unknown>) => void {
  const context = useApiContext();
  const owner = context.captureMutationFeedbackOwner();
  return useCallback((result: WriteCommandResult<unknown>) => {
    context.reportMutationFeedback(scope, owner, result);
  }, [context.reportMutationFeedback, owner, scope]);
}

export interface PresentationLocationState {
  capture(): PresentationLocationOwner;
  owns(owner: PresentationLocationOwner): boolean;
}

export function usePresentationLocationAuthority(): PresentationLocationState {
  const context = useApiContext();
  return { capture: context.captureMutationFeedbackOwner, owns: context.ownsMutationFeedbackOwner };
}

export interface MutationFeedbackState {
  result: WriteCommandResult<unknown> | null;
  clear(): void;
}

export function useMutationFeedbackState(): MutationFeedbackState {
  const context = useApiContext();
  return { result: context.mutationFeedback, clear: context.clearMutationFeedback };
}

function useApiContext(): ApiContextValue {
  const value = useContext(ApiContext);
  if (value === null) throw new Error("ApiClientProvider is missing");
  return value;
}
