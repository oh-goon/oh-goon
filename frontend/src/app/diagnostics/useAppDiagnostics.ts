import { useSyncExternalStore } from "react";

import { appDiagnosticStore, type AppDiagnostic } from "@/domain/diagnostics/appDiagnosticStore";

export function useAppDiagnostics(): readonly AppDiagnostic[] {
  return useSyncExternalStore(
    appDiagnosticStore.subscribe,
    appDiagnosticStore.getSnapshot,
    appDiagnosticStore.getSnapshot,
  );
}
