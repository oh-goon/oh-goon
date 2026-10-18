import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useApiClient } from "@/app/ApiClientProvider";
import { recordReadFailure } from "@/app/reads/recordReadFailure";
import { RecoveryPanel } from "@/components/common/RecoveryPanel";
import type { ApiClient } from "@/domain/api/ApiClient";
import type { AppBootstrapState } from "@/domain/bootstrap/AppBootstrapState";

interface BootstrapContextValue {
  readonly state: AppBootstrapState;
  retry(): void;
}

const BootstrapContext = createContext<BootstrapContextValue | null>(null);

export function AppBootstrapProvider({ children }: { children: ReactNode }) {
  const client = useApiClient();
  const { t } = useTranslation();
  const [state, setState] = useState<AppBootstrapState>({ status: "boot-starting" });
  const [attempt, setAttempt] = useState(0);
  const controllerRef = useRef<AbortController | null>(null);
  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    void bootstrap(client, controller.signal, setState, {
      backend: t("bootstrap.backendError"),
      fleet: t("bootstrap.fleetError"),
    });
    return () => controller.abort();
  }, [attempt, client, t]);

  const value = useMemo<BootstrapContextValue>(() => ({ state, retry }), [retry, state]);
  if (state.status === "boot-backend-unavailable" || state.status === "boot-recoverable-error") {
    return <BootstrapContext.Provider value={value}><main className="fatal-recovery"><RecoveryPanel
      tone="warning"
      live="polite"
      title={state.status === "boot-backend-unavailable" ? t("bootstrap.backendTitle") : t("bootstrap.recoveryTitle")}
      message={state.failure.message}
      actions={<button className="button button--primary" type="button" onClick={retry}>{t("bootstrap.retry")}</button>}
    /></main></BootstrapContext.Provider>;
  }
  if (state.status !== "boot-ready" && state.status !== "boot-configuration-required") {
    return <BootstrapContext.Provider value={value}><main className="fatal-recovery" role="status"><div className="panel"><span className="eyebrow">{t("bootstrap.eyebrow")}</span><h1>{t("bootstrap.title")}</h1><p>{bootstrapMessage(state, t)}</p></div></main></BootstrapContext.Provider>;
  }
  return <BootstrapContext.Provider value={value}>{children}</BootstrapContext.Provider>;
}

export function useAppBootstrap(): BootstrapContextValue {
  const value = useContext(BootstrapContext);
  if (value === null) throw new Error("AppBootstrapProvider is missing");
  return value;
}

interface BootstrapMessages { readonly backend: string; readonly fleet: string }

type StateWriter = (state: AppBootstrapState) => void;

async function bootstrap(client: ApiClient, signal: AbortSignal, write: StateWriter, messages: BootstrapMessages): Promise<void> {
  try {
    write({ status: "boot-checking-backend" });
    await client.health({ signal });
    if (signal.aborted) return;
  } catch (error) {
    if (signal.aborted) return;
    write({ status: "boot-backend-unavailable", failure: recordReadFailure("GET /health bootstrap", error, messages.backend, "health", 0) });
    return;
  }
  try {
    write({ status: "boot-loading-configuration" });
    const locations = await client.locations({ signal });
    if (signal.aborted) return;
    if (locations.locations.length === 0) {
      write({ status: "boot-configuration-required" });
      return;
    }
    write({ status: "boot-loading-fleet" });
    const fleet = await client.fleet({ signal });
    if (signal.aborted) return;
    write({ status: "boot-ready", fleet });
  } catch (error) {
    if (signal.aborted) return;
    write({ status: "boot-recoverable-error", failure: recordReadFailure("GET /fleet bootstrap", error, messages.fleet, "fleet", 0) });
  }
}

function bootstrapMessage(state: AppBootstrapState, t: (key: string) => string): string {
  if (state.status === "boot-checking-backend") return t("bootstrap.checkingBackend");
  if (state.status === "boot-loading-configuration") return t("bootstrap.loadingConfiguration");
  if (state.status === "boot-loading-fleet") return t("bootstrap.loadingFleet");
  return t("bootstrap.starting");
}
