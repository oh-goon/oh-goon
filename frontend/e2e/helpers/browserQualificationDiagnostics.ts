import http from "node:http";

export type BrowserQualificationCode =
  | "BROWSER_NETWORK_EPOCH_CHANGED"
  | "BROWSER_TRANSPORT_UNAVAILABLE"
  | "BROWSER_FIXTURE_NAVIGATION_FAILED";

export interface FixtureOriginProbe {
  readonly ok: boolean;
  readonly statusCode: number | null;
  readonly code: string | null;
  readonly elapsedMs: number;
}

export class BrowserQualificationError extends Error {
  readonly code: BrowserQualificationCode;

  constructor(code: BrowserQualificationCode, message: string, options: { cause?: unknown } = {}) {
    super(`${code}: ${message}`, options.cause === undefined ? undefined : { cause: options.cause });
    this.name = "BrowserQualificationError";
    this.code = code;
  }
}

export function boundedBrowserEvidence(evidence: readonly string[], limit = 20): string[] {
  return evidence.slice(-limit);
}

export function classifyBrowserNavigationFailure(
  evidence: readonly string[],
  error: unknown,
): BrowserQualificationCode {
  const rendered = `${error instanceof Error ? `${error.name}:${error.message}` : String(error)}\n${evidence.join("\n")}`;
  return rendered.includes("ERR_NETWORK_CHANGED")
    ? "BROWSER_NETWORK_EPOCH_CHANGED"
    : "BROWSER_FIXTURE_NAVIGATION_FAILED";
}

export function refineBrowserNavigationFailure(
  classification: BrowserQualificationCode,
  probe: FixtureOriginProbe,
): BrowserQualificationCode {
  if (classification === "BROWSER_NETWORK_EPOCH_CHANGED") return classification;
  return probe.ok ? classification : "BROWSER_TRANSPORT_UNAVAILABLE";
}

export function probeFixtureOrigin(url: string, deadlineMs = 750): Promise<FixtureOriginProbe> {
  const started = performance.now();
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: Omit<FixtureOriginProbe, "elapsedMs">) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ ...result, elapsedMs: Math.round(performance.now() - started) });
    };
    const request = http.request(url, { method: "GET" }, (response) => {
      response.resume();
      finish({ ok: response.statusCode === 200, statusCode: response.statusCode ?? null, code: null });
      request.destroy();
    });
    const timer = setTimeout(() => {
      const timeout = new Error(`fixture origin probe exceeded ${deadlineMs} ms`) as NodeJS.ErrnoException;
      timeout.code = "OH_GOON_BROWSER_FIXTURE_HTTP_TIMEOUT";
      request.destroy(timeout);
    }, deadlineMs);
    request.once("error", (error: NodeJS.ErrnoException) => finish({
      ok: false,
      statusCode: null,
      code: error.code ?? "HTTP_REQUEST_FAILED",
    }));
    request.end();
  });
}
