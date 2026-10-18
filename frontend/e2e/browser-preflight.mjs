import { chromium, firefox, webkit } from "@playwright/test";

const requestedEngine = process.env.OH_GOON_BROWSER_ENGINE?.trim().toLowerCase() || "all";
const chromiumExecutable = process.env.OH_GOON_CHROMIUM_EXECUTABLE?.trim() || undefined;
const available = {
  chromium: ["Chromium", chromium, chromiumExecutable ? { executablePath: chromiumExecutable } : {}],
  firefox: ["Firefox", firefox, {}],
  webkit: ["WebKit", webkit, {}],
};

const MAX_ERROR_LINES = 18;
const MAX_ERROR_CHARS = 6000;

function boundedFailure(error) {
  const raw = error instanceof Error ? error.message : String(error);
  const lines = raw.split("\n").slice(0, MAX_ERROR_LINES);
  const bounded = lines.join("\n").slice(0, MAX_ERROR_CHARS);
  return bounded || "unknown browser launch failure";
}

if (requestedEngine !== "all" && !(requestedEngine in available)) {
  console.error(`Unsupported OH_GOON_BROWSER_ENGINE: ${requestedEngine}`);
  process.exitCode = 2;
} else {
  const browserTypes = requestedEngine === "all"
    ? Object.values(available)
    : [available[requestedEngine]];
  const failures = [];
  for (const [name, browserType, launchOptions] of browserTypes) {
    try {
      const browser = await browserType.launch({ headless: true, ...launchOptions });
      await browser.close();
    } catch (error) {
      failures.push([name, boundedFailure(error)]);
    }
  }

  if (failures.length > 0) {
    console.error("Browser acceptance cannot start because its requested engine(s) are unavailable:");
    for (const [name, detail] of failures) {
      console.error(`  - ${name}:`);
      for (const line of detail.split("\n")) console.error(`      ${line}`);
    }
    console.error("Run `make browser-setup` for browser binaries. If the report points to missing host libraries, run the explicit privileged `make browser-system-deps`. You can also use OH_GOON_CHROMIUM_EXECUTABLE for local Chromium or `make acceptance-browser-container`.");
    process.exitCode = 2;
  } else {
    console.log(`Browser preflight passed: ${requestedEngine === "all" ? "Chromium, Firefox and WebKit" : requestedEngine} launched successfully.`);
  }
}
