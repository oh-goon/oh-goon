import { expect, type ConsoleMessage, type Page, type Request } from "@playwright/test";
import { DEFAULT_RUN_ID, SCALE_RUN_ID } from "../fixtures/runIds";
import { FIXTURE_NOW } from "../fixtures/time";
import type { BrowserFixtureScenario } from "../fixtures/scenarios";
import {
  boundedBrowserEvidence,
  BrowserQualificationError,
  classifyBrowserNavigationFailure,
  probeFixtureOrigin,
  refineBrowserNavigationFailure,
} from "./browserQualificationDiagnostics";
import { FRONTEND_VERSION } from "./frontendVersion";
import { gameTapeRegion } from "./locators";


async function prepareFixtureClock(page: Page): Promise<void> {
  await page.clock.setFixedTime(new Date(FIXTURE_NOW));
}

async function navigateToFixture(page: Page, url: string): Promise<void> {
  const evidence: string[] = [];
  let rejectNetworkChange: ((error: BrowserQualificationError) => void) | null = null;
  let networkChangeObserved = false;
  const networkChange = new Promise<never>((_resolve, reject) => {
    rejectNetworkChange = reject;
  });
  const onPageError = (error: Error) => evidence.push(`pageerror:${error.message}`);
  const onConsole = (message: ConsoleMessage) => { if (message.type() === "error") evidence.push(`console:${message.text()}`); };
  const onRequestFailed = (request: Request) => {
    const errorText = request.failure()?.errorText ?? "unknown";
    evidence.push(`requestfailed:${request.url()}:${errorText}`);
    if (!networkChangeObserved && errorText.includes("ERR_NETWORK_CHANGED")) {
      networkChangeObserved = true;
      rejectNetworkChange?.(new BrowserQualificationError(
        "BROWSER_NETWORK_EPOCH_CHANGED",
        `browser network generation changed while loading ${request.url()}`,
      ));
    }
  };
  page.on("pageerror", onPageError); page.on("console", onConsole); page.on("requestfailed", onRequestFailed);
  try {
    await Promise.race([
      (async () => {
        await page.goto(url);
        await page.locator("main h1").waitFor({ state: "visible" });
      })(),
      networkChange,
    ]);
  } catch (error) {
    const classification = classifyBrowserNavigationFailure(evidence, error);
    const state = await page.evaluate(() => ({ readyState: document.readyState, version: document.documentElement.getAttribute("data-oh-goon-version"), body: document.body?.innerText.slice(0, 1200) ?? "" })).catch(() => ({ readyState: "unavailable", version: null, body: "" }));
    const currentUrl = page.url();
    const target = new URL(url, currentUrl.startsWith("http") ? currentUrl : "http://127.0.0.1:5173");
    target.hash = "";
    const fixtureProbe = await probeFixtureOrigin(target.toString());
    const finalClassification = refineBrowserNavigationFailure(classification, fixtureProbe);
    throw new BrowserQualificationError(
      finalClassification,
      `fixture navigation failed before functional assertion; url=${currentUrl} state=${JSON.stringify(state)} fixtureProbe=${JSON.stringify(fixtureProbe)} evidence=${JSON.stringify(boundedBrowserEvidence(evidence))}`,
      { cause: error },
    );
  } finally {
    page.off("pageerror", onPageError); page.off("console", onConsole); page.off("requestfailed", onRequestFailed);
  }
}

export async function openFixture(page: Page, route: string, scenario: BrowserFixtureScenario = "default"): Promise<void> {
  await prepareFixtureClock(page);
  await navigateToFixture(page, `/e2e/fixture.html?scenario=${scenario}#${route}`);
  await expect(page.locator("html")).toHaveAttribute("data-oh-goon-version", FRONTEND_VERSION);
}

interface OpenRunFixtureOptions {
  readonly scenario?: BrowserFixtureScenario;
  readonly runId?: string;
  readonly stepId?: string;
  readonly view?: "summary" | "timeline" | "flow" | "logs" | "outputs" | "verification" | "enforcer";
}

/** Open one exact Run review. Plain #runs intentionally remains history-first. */
export async function openRunFixture(
  page: Page,
  options: OpenRunFixtureOptions = {},
): Promise<void> {
  const scenario = options.scenario ?? "default";
  const selectedRunId = options.runId ?? (scenario === "scale" ? SCALE_RUN_ID : DEFAULT_RUN_ID);
  const view = options.view ?? "flow";
  const stepQuery = options.stepId ? `&step=${encodeURIComponent(options.stepId)}` : "";
  await prepareFixtureClock(page);
  await navigateToFixture(page, `/e2e/fixture.html?scenario=${scenario}#runs?run=${encodeURIComponent(selectedRunId)}&view=${view}${stepQuery}`);
  await expect(page.locator("html")).toHaveAttribute("data-oh-goon-version", FRONTEND_VERSION);
  await gameTapeRegion(page).waitFor({ state: "visible" });
}

export async function enableAdvancedExperience(page: Page): Promise<void> {
  await page.addInitScript(() => window.localStorage.setItem("oh-goon.experience", "advanced"));
}

export async function expectExplicitRunRoute(
  page: Page,
  options: { readonly repository?: boolean } = {},
): Promise<string> {
  await expect.poll(() => new URL(page.url()).hash).toMatch(/^#runs\?/);
  const params = new URLSearchParams(new URL(page.url()).hash.split("?", 2)[1] ?? "");
  const runId = params.get("run");
  expect(runId, "Runs drill-down must carry an explicit opaque run identity").toBeTruthy();
  if (options.repository === true) {
    expect(params.get("repository"), "Repository drill-down must carry an explicit repository identity").toBeTruthy();
  }
  return runId!;
}
