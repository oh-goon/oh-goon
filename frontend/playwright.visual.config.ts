import baseConfig from "./playwright.config";
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  ...baseConfig,
  testDir: "./e2e",
  testMatch: /.*\.visual\.ts/,
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
});
