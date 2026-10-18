import { readFile } from "node:fs/promises";
import { verifyLocalhostTransport } from "./browser-localhost-transport.mjs";

async function isWsl() {
  if (process.platform !== "linux") return false;
  if (process.env.WSL_DISTRO_NAME || process.env.WSL_INTEROP) return true;
  try {
    return /microsoft|wsl/i.test(await readFile("/proc/version", "utf8"));
  } catch {
    return false;
  }
}

try {
  const result = await verifyLocalhostTransport();
  console.log(
    `Browser localhost transport preflight passed: ${result.host} bound a live Node TCP listener, exchanged repository-owned payload bytes, and released listener ownership.`,
  );
} catch (error) {
  const code = error?.code || "UNKNOWN";
  console.error("BROWSER LOCALHOST TRANSPORT: FAIL");
  console.error("Browser qualification cannot start.");
  console.error("Node localhost TCP transport did not satisfy the required bind/connect/accept/payload/release contract.");
  console.error(`Observed: ${code}${error?.message ? ` — ${error.message}` : ""}`);
  console.error("No Playwright or browser tests were started by this gate.");
  if (await isWsl()) {
    console.error("WSL detected. The live loopback listener/payload proof failed in Node before Playwright startup.");
  }
  process.exitCode = 2;
}
