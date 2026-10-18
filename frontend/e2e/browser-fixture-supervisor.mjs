import http from "node:http";
import net from "node:net";
import { performance } from "node:perf_hooks";
import { setTimeout as delay } from "node:timers/promises";
import { LOCALHOST_IPV4, waitForSocketConnect } from "./localhost-tcp-probe.mjs";

export const BROWSER_FIXTURE_HOST = LOCALHOST_IPV4;
export const BROWSER_FIXTURE_PORT = 5173;
export const BROWSER_FIXTURE_URL = `http://${BROWSER_FIXTURE_HOST}:${BROWSER_FIXTURE_PORT}/e2e/fixture.html`;
export const DEFAULT_FIXTURE_STARTUP_DEADLINE_MS = 30_000;
export const DEFAULT_PROBE_DEADLINE_MS = 750;
export const DEFAULT_STARTUP_TAIL_LINES = 40;

export class BrowserFixtureStartupError extends Error {
  constructor(code, message, evidence = {}) {
    super(`${code}: ${message}`);
    this.name = "BrowserFixtureStartupError";
    this.code = code;
    this.evidence = evidence;
  }
}

export function redactDiagnosticLine(line) {
  return String(line)
    .replace(/(authorization:\s*(?:bearer\s+)?)[^\s]+/gi, "$1<redacted>")
    .replace(/\b(token|password|secret|api[_-]?key)\s*([=:])\s*[^\s]+/gi, "$1$2<redacted>")
    .replace(/:\/\/([^\s/:@]+):([^\s/@]+)@/g, "://$1:<redacted>@");
}

export function boundedStartupTail(lines, limit = DEFAULT_STARTUP_TAIL_LINES) {
  return lines.slice(-limit).map(redactDiagnosticLine);
}

export async function probeTcp({
  host = BROWSER_FIXTURE_HOST,
  port = BROWSER_FIXTURE_PORT,
  deadlineMs = DEFAULT_PROBE_DEADLINE_MS,
  createConnection = (options) => net.createConnection(options),
} = {}) {
  const socket = createConnection({ host, port });
  try {
    const result = await waitForSocketConnect({ socket, deadlineMs, host, port });
    return { ok: true, elapsedMs: result.elapsedMs };
  } catch (error) {
    return {
      ok: false,
      code: error?.code ?? "TCP_CONNECT_FAILED",
      message: error instanceof Error ? error.message : String(error),
    };
  }
}

export function probeHttp({
  url = BROWSER_FIXTURE_URL,
  deadlineMs = DEFAULT_PROBE_DEADLINE_MS,
  request = http.request,
} = {}) {
  return new Promise((resolve) => {
    const started = performance.now();
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ ...result, elapsedMs: performance.now() - started });
    };
    const req = request(url, { method: "GET" }, (response) => {
      response.resume();
      finish({ ok: response.statusCode === 200, statusCode: response.statusCode ?? null });
      req.destroy();
    });
    const timer = setTimeout(() => {
      const error = new Error(`fixture HTTP probe did not settle within ${deadlineMs} ms`);
      error.code = "OH_GOON_BROWSER_FIXTURE_HTTP_TIMEOUT";
      req.destroy(error);
    }, deadlineMs);
    req.once("error", (error) => finish({
      ok: false,
      code: error?.code ?? "HTTP_REQUEST_FAILED",
      message: error instanceof Error ? error.message : String(error),
    }));
    req.end();
  });
}

export async function waitForFixtureReady({
  child,
  deadlineMs = DEFAULT_FIXTURE_STARTUP_DEADLINE_MS,
  intervalMs = 100,
  tcpProbe = probeTcp,
  httpProbe = probeHttp,
  now = () => performance.now(),
  sleep = delay,
} = {}) {
  const started = now();
  let exit = null;
  let spawnError = null;
  let firstBoundMs = null;
  let lastTcp = null;
  let lastHttp = null;
  const onExit = (code, signal) => { exit = { code, signal }; };
  const onError = (error) => { spawnError = error; };
  child.once("exit", onExit);
  child.once("error", onError);
  try {
    while (now() - started < deadlineMs) {
      if (spawnError !== null) {
        throw new BrowserFixtureStartupError(
          "BROWSER_FIXTURE_PROCESS_SPAWN_FAILED",
          "fixture process could not be spawned",
          {
            elapsedMs: Math.round(now() - started),
            code: spawnError?.code ?? null,
            message: spawnError instanceof Error ? spawnError.message : String(spawnError),
          },
        );
      }
      if (exit !== null) {
        throw new BrowserFixtureStartupError(
          "BROWSER_FIXTURE_PROCESS_EXITED",
          "fixture process exited before readiness",
          { elapsedMs: Math.round(now() - started), exit, firstBoundMs },
        );
      }
      lastTcp = await tcpProbe();
      if (lastTcp.ok) {
        if (firstBoundMs === null) firstBoundMs = Math.round(now() - started);
        lastHttp = await httpProbe();
        if (lastHttp.ok) {
          return {
            elapsedMs: Math.round(now() - started),
            firstBoundMs,
            httpStatus: lastHttp.statusCode,
          };
        }
      }
      await sleep(intervalMs);
    }
    const code = firstBoundMs === null
      ? "BROWSER_FIXTURE_PORT_NOT_BOUND"
      : "BROWSER_FIXTURE_HTTP_NOT_READY";
    const message = firstBoundMs === null
      ? "fixture port never became reachable before the startup deadline"
      : "fixture port became reachable but the fixture HTTP endpoint never became ready";
    throw new BrowserFixtureStartupError(code, message, {
      deadlineMs,
      elapsedMs: Math.round(now() - started),
      firstBoundMs,
      lastTcp,
      lastHttp,
    });
  } finally {
    child.off("exit", onExit);
    child.off("error", onError);
  }
}
