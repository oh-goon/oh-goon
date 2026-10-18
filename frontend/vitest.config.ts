import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

const domBackedTypeScriptTests = [
  "src/app/api/httpTransport.test.ts",
  "src/app/api/localAccess.test.ts",
  "src/app/fleetRun/watchFleetRun.test.ts",
  "src/app/pages/runReviewState.test.ts",
];

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "@generated": fileURLToPath(new URL("../generated/frontend", import.meta.url)),
      "@test": fileURLToPath(new URL("./tests/support", import.meta.url)),
    },
  },
  test: {
    globals: true,
    exclude: ["e2e/**/*.spec.ts", "e2e-runtime/**", "node_modules/**", "dist/**"],
    clearMocks: true,
    restoreMocks: true,
    // Keep APP certification memory-bounded and transport-stable on developer workstations.
    // Threads avoid expensive fork startup on WSL Windows-mounted workspaces. Run files
    // sequentially because Node/Vitest worker exits under parallel file load are runner
    // failures, not test failures; per-file isolation and within-file semantics remain intact.
    pool: "threads",
    fileParallelism: false,
    testTimeout: 15_000,
    projects: [
      {
        extends: true,
        test: {
          name: "node",
          environment: "node",
          include: ["src/**/*.test.ts", "e2e/**/*.test.ts"],
          exclude: domBackedTypeScriptTests,
          setupFiles: [],
        },
      },
      {
        extends: true,
        test: {
          name: "dom",
          environment: "jsdom",
          include: ["src/**/*.test.tsx", ...domBackedTypeScriptTests],
          setupFiles: ["./tests/setup.ts"],
        },
      },
    ],
  },
});
