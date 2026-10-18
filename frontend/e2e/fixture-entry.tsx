import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { ApiClientProvider } from "@/app/ApiClientProvider";
import { App } from "@/app/App";
import { AppBootstrapProvider } from "@/app/bootstrap/AppBootstrapProvider";
import { AppErrorBoundary } from "@/app/errors/AppErrorBoundary";
import { FleetRunProvider } from "@/app/FleetRunProvider";
import { applyThemeTokens } from "@/app/theme/themeTokens";
import "@/app/appStyles";
import "@/i18n/setup";
import { BrowserFixtureApiClient } from "./BrowserFixtureApiClient";
import { browserFixtureScenario } from "./fixtures/scenarios";
import packageJson from "../package.json";

const root = document.getElementById("root");
if (root === null) throw new Error("Missing #root element");

const requestedScenario = new URLSearchParams(window.location.search).get("scenario");
const scenario = browserFixtureScenario(requestedScenario);
applyThemeTokens(document.documentElement);
document.documentElement.dataset.e2eScenario = scenario;
document.documentElement.dataset.ohGoonVersion = packageJson.version;

createRoot(root).render(
  <StrictMode>
    <AppErrorBoundary>
      <ApiClientProvider client={new BrowserFixtureApiClient(scenario)}>
        <AppBootstrapProvider>
          <FleetRunProvider>
            <App />
          </FleetRunProvider>
        </AppBootstrapProvider>
      </ApiClientProvider>
    </AppErrorBoundary>
  </StrictMode>,
);
