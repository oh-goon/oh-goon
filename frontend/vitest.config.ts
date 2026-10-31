import { lstatSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vitest/config";

const domBackedTypeScriptTests = [
  "src/app/api/httpTransport.test.ts",
  "src/app/api/localAccess.test.ts",
  "src/app/fleetRun/watchFleetRun.test.ts",
  "src/app/pages/runReviewState.test.ts",
];

const nativeJsonReport = process.env.OH_GOON_VITEST_JSON_REPORT;
delete process.env.OH_GOON_VITEST_JSON_REPORT;
if (nativeJsonReport !== undefined) {
  const checkout = fileURLToPath(new URL("..", import.meta.url));
  const parent = dirname(nativeJsonReport);
  const fromCheckout = relative(checkout, nativeJsonReport);
  if (
    !isAbsolute(nativeJsonReport) ||
    resolve(nativeJsonReport) !== nativeJsonReport ||
    (fromCheckout !== ".." && !fromCheckout.startsWith(`..${sep}`) && !isAbsolute(fromCheckout)) ||
    realpathSync(parent) !== parent
  ) {
    throw new Error("Vitest JSON reports require a canonical absolute path outside source");
  }
  const metadata = lstatSync(parent);
  if (
    !metadata.isDirectory() ||
    (metadata.mode & 0o077) !== 0 ||
    (typeof process.getuid === "function" && metadata.uid !== process.getuid())
  ) {
    throw new Error("Vitest JSON report directory must be private and caller owned");
  }
  try {
    lstatSync(nativeJsonReport);
    throw new Error("Vitest JSON report must use a fresh destination");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
}

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
      "@generated": fileURLToPath(new URL("../generated/frontend", import.meta.url)),
      "@test": fileURLToPath(new URL("./tests/support", import.meta.url)),
    },
  },
  test: {
    ...(nativeJsonReport === undefined
      ? {}
      : { reporters: ["default", "json"], outputFile: { json: nativeJsonReport } }),
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
