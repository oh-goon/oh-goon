import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { App } from "@/app/App";
import { ensureLocalAccess } from "@/app/api/localAccess";
import { AppBootstrapProvider } from "@/app/bootstrap/AppBootstrapProvider";
import { AppErrorBoundary } from "@/app/errors/AppErrorBoundary";
import { FleetRunProvider } from "@/app/FleetRunProvider";
import { applyThemeTokens } from "@/app/theme/themeTokens";
import "@/i18n/setup";
import "@/app/appStyles";

const root = document.getElementById("root");
if (root === null) throw new Error("Missing #root element");

applyThemeTokens(document.documentElement);

void boot();

async function boot(): Promise<void> {
  const access = await ensureLocalAccess().catch(() => ({ status: "required" as const }));
  if (access.status !== "ready") {
    root!.innerHTML = "<main><h1>Local access required</h1><p>Open Oh Goon from the launch URL printed by <code>oh-goon app start</code> or <code>make dev</code>.</p></main>";
    return;
  }

  createRoot(root!).render(
    <StrictMode>
      <AppErrorBoundary>
        <ApiClientProvider>
          <AppBootstrapProvider>
            <FleetRunProvider>
              <App />
            </FleetRunProvider>
          </AppBootstrapProvider>
        </ApiClientProvider>
      </AppErrorBoundary>
    </StrictMode>,
  );
}
