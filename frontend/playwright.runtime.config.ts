import { defineConfig, devices } from "@playwright/test";

const runtimeUrl = process.env.OH_GOON_RUNTIME_URL ?? "http://127.0.0.1:8787";
const runtimeAccessToken = process.env.OH_GOON_RUNTIME_ACCESS_TOKEN;

export default defineConfig({
  testDir: "./e2e-runtime",
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-runtime-report" }]],
  globalSetup: "./e2e-runtime/globalSetup.ts",
  expect: { timeout: 8_000 },
  use: {
    baseURL: runtimeUrl,
    extraHTTPHeaders: runtimeAccessToken ? { "X-Oh-Goon-Local-Access": runtimeAccessToken } : {},
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "firefox", use: { ...devices["Desktop Firefox"], viewport: { width: 1440, height: 900 } } },
    { name: "webkit", use: { ...devices["Desktop Safari"], viewport: { width: 1440, height: 900 } } },
  ],
});
