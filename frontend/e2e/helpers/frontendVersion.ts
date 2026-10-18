import { readFileSync } from "node:fs";

interface FrontendPackageMetadata {
  readonly version?: unknown;
}

function readFrontendVersion(): string {
  const packageUrl = new URL("../../package.json", import.meta.url);
  const parsed = JSON.parse(readFileSync(packageUrl, "utf8")) as FrontendPackageMetadata;
  if (typeof parsed.version !== "string" || parsed.version.length === 0) {
    throw new Error("frontend/package.json must contain a non-empty version");
  }
  return parsed.version;
}

export const FRONTEND_VERSION = readFrontendVersion();
