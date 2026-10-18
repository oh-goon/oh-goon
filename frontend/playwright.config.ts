import { defineConfig, devices } from "@playwright/test";

const chromiumExecutable = process.env.OH_GOON_CHROMIUM_EXECUTABLE?.trim() || undefined;
const managedFixtureServer = process.env.OH_GOON_BROWSER_MANAGED_SERVER === "1";

const fixtureUrl = "http://127.0.0.1:5173/e2e/fixture.html";
const compatibilitySmoke = /(accessibility|content-visibility|routes)\.spec\.ts/;
const responsiveSmoke = /(content-visibility|fleet-scale|routes)\.spec\.ts/;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts/,
  // Keep each spec internally sequential so heavy fleet/browser scenarios do not
  // compete with another test from the same file for memory. Separate spec files
  // may still run in parallel through the bounded worker pool below.
  fullyParallel: false,
  // Browser certification must fit a normal developer workstation. Two workers
  // preserve useful cross-file parallelism without launching a browser per core.
  workers: 2,
  forbidOnly: true,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  expect: { timeout: 5_000 },
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}{ext}",
  use: {
    baseURL: fixtureUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: managedFixtureServer ? undefined : {
    command: "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort",
    url: fixtureUrl,
    reuseExistingServer: process.env.OH_GOON_BROWSER_REUSE_SERVER === "1",
    timeout: 30_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], ...(chromiumExecutable ? { executablePath: chromiumExecutable } : {}), viewport: { width: 1440, height: 900 } } },
    {
      name: "firefox",
      testMatch: compatibilitySmoke,
      use: { ...devices["Desktop Firefox"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "webkit",
      testMatch: compatibilitySmoke,
      use: { ...devices["Desktop Safari"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "mobile",
      testMatch: responsiveSmoke,
      use: {
        ...devices["Desktop Chrome"],
        ...(chromiumExecutable ? { executablePath: chromiumExecutable } : {}),
        viewport: { width: 390, height: 844 },
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: "tablet",
      testMatch: responsiveSmoke,
      use: {
        ...devices["Desktop Chrome"],
        ...(chromiumExecutable ? { executablePath: chromiumExecutable } : {}),
        viewport: { width: 768, height: 1024 },
        hasTouch: true,
      },
    },
  ],
});
