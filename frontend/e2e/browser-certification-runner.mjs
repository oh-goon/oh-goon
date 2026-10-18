import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import {
  BROWSER_FIXTURE_HOST,
  BROWSER_FIXTURE_PORT,
  BROWSER_FIXTURE_URL,
  boundedStartupTail,
  waitForFixtureReady,
} from "./browser-fixture-supervisor.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = dirname(here);
const npmExecutable = process.platform === "win32" ? "npm.cmd" : "npm";
const startupLines = [];
let fixture = null;
let cleaning = false;

function capture(stream, label) {
  let pending = "";
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    pending += chunk;
    const lines = pending.split(/\r?\n/);
    pending = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) startupLines.push(`${label}:${line}`);
      if (startupLines.length > 200) startupLines.splice(0, startupLines.length - 200);
    }
  });
  stream.on("end", () => {
    if (pending.trim()) startupLines.push(`${label}:${pending}`);
  });
}

async function stopFixture() {
  if (cleaning || fixture === null || fixture.exitCode !== null) return;
  cleaning = true;
  try {
    if (process.platform !== "win32" && fixture.pid) {
      try { process.kill(-fixture.pid, "SIGTERM"); } catch {}
    } else {
      fixture.kill("SIGTERM");
    }
    const deadline = Date.now() + 3000;
    while (fixture.exitCode === null && Date.now() < deadline) await delay(50);
    if (fixture.exitCode === null) {
      if (process.platform !== "win32" && fixture.pid) {
        try { process.kill(-fixture.pid, "SIGKILL"); } catch {}
      } else {
        fixture.kill("SIGKILL");
      }
    }
  } finally {
    cleaning = false;
  }
}

function printStartupFailure(error) {
  console.error("BROWSER FIXTURE STARTUP FAILED");
  console.error(`classification=${error?.code ?? "BROWSER_FIXTURE_STARTUP_FAILED"}`);
  console.error(`fixture=${BROWSER_FIXTURE_URL}`);
  if (error?.evidence) console.error(`evidence=${JSON.stringify(error.evidence)}`);
  const tail = boundedStartupTail(startupLines);
  if (tail.length) {
    console.error("startup-tail-begin");
    for (const line of tail) console.error(line);
    console.error("startup-tail-end");
  }
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, async () => {
    await stopFixture();
    process.exit(128 + (signal === "SIGINT" ? 2 : 15));
  });
}

try {
  const startedAt = Date.now();
  console.log(`BROWSER_FIXTURE_SPAWN host=${BROWSER_FIXTURE_HOST} port=${BROWSER_FIXTURE_PORT}`);
  fixture = spawn(
    npmExecutable,
    ["run", "dev", "--", "--host", BROWSER_FIXTURE_HOST, "--port", String(BROWSER_FIXTURE_PORT), "--strictPort"],
    {
      cwd: frontendRoot,
      env: process.env,
      detached: process.platform !== "win32",
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  capture(fixture.stdout, "stdout");
  capture(fixture.stderr, "stderr");

  const readiness = await waitForFixtureReady({ child: fixture });
  console.log(
    `BROWSER_FIXTURE_READY elapsed_ms=${readiness.elapsedMs} first_bound_ms=${readiness.firstBoundMs} http=${readiness.httpStatus}`,
  );

  const playwright = spawn(
    npmExecutable,
    ["run", "playwright:test", "--", ...process.argv.slice(2)],
    {
      cwd: frontendRoot,
      env: { ...process.env, OH_GOON_BROWSER_MANAGED_SERVER: "1" },
      stdio: "inherit",
    },
  );
  const result = await new Promise((resolve) => {
    playwright.once("exit", (code, signal) => resolve({ code, signal }));
  });
  console.log(`BROWSER_CERTIFICATION_SETTLED elapsed_ms=${Date.now() - startedAt} code=${result.code ?? "null"} signal=${result.signal ?? "none"}`);
  await stopFixture();
  process.exitCode = result.code ?? 1;
} catch (error) {
  printStartupFailure(error);
  await stopFixture();
  process.exitCode = 1;
}
