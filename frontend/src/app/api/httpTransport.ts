import { ApiError, ApiUnavailableError } from "@/domain/api/ApiError";
import type { ReadRequestOptions } from "@/domain/api/ApiClient";
import {
  decodeResponse,
  operationBody,
  operations,
  operationUrl,
  type OperationCall,
  type OperationName,
  type OperationResponse,
} from "@generated/operations";
import { decodeTransportError } from "@generated/validators";

const API_ROOT = "/api";
const REQUEST_TIMEOUT_MS = 10_000;
const READ_RETRY_DELAYS_MS = [250, 750] as const;

export interface OperationTransportOptions {
  readonly timeoutMs?: number;
}

export function readOperation<Name extends OperationName>(
  name: Name,
  call: OperationCall<Name>,
  options?: ReadRequestOptions,
): Promise<OperationResponse<Name>> {
  return executeOperation(name, call, options?.signal);
}


export async function requestLocalSession(token?: string): Promise<boolean> {
  const response = await fetchApiRaw("/local-session", {
    method: token === undefined ? "GET" : "POST",
    ...(token === undefined
      ? {}
      : { headers: { "X-Oh-Goon-Local-Access": token } }),
    credentials: "same-origin",
  });
  return response.ok;
}

export async function executeOperation<Name extends OperationName>(
  name: Name,
  call: OperationCall<Name>,
  signal?: AbortSignal,
  options?: OperationTransportOptions,
): Promise<OperationResponse<Name>> {
  const metadata = operations[name];
  const url = operationUrl(name, call);
  const body = operationBody(name, call);
  const init: RequestInit = {
    method: metadata.method,
    ...(body === undefined ? {} : { body }),
    ...(signal === undefined ? {} : { signal }),
  };
  const retryDelays = metadata.method === "GET" ? READ_RETRY_DELAYS_MS : [];
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await requestOnce(name, url, init, options);
    } catch (error) {
      if (!(error instanceof ApiUnavailableError)) throw error;
      const retryDelay = retryDelays[attempt];
      if (retryDelay === undefined) throw error;
      await delay(retryDelay, signal);
    }
  }
}

async function requestOnce<Name extends OperationName>(
  name: Name,
  url: string,
  init: RequestInit,
  options?: OperationTransportOptions,
): Promise<OperationResponse<Name>> {
  const response = await requestRaw(url, init, options);
  const kind = operations[name].responseKind;
  if (kind === "void") return undefined as OperationResponse<Name>;
  const raw = await response.text();
  if (kind === "raw") return raw as OperationResponse<Name>;
  if (response.status === 204) {
    throw new ApiError("Expected response payload", 204, "INVALID_API_CONTRACT", "INTERNAL");
  }
  return decodeResponse(name, parseJson(raw));
}

async function requestRaw(url: string, init: RequestInit, options?: OperationTransportOptions): Promise<Response> {
  const response = await fetchApiRaw(url, init, options);
  if (!response.ok) throw await responseError(response);
  return response;
}

async function fetchApiRaw(url: string, init: RequestInit, options?: OperationTransportOptions): Promise<Response> {
  const timeoutMs = options?.timeoutMs ?? REQUEST_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new RangeError("Request timeout must be positive and finite");
  const externalSignal = init.signal;
  if (externalSignal?.aborted) throw abortError();
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) {
    controller.abort(externalSignal.reason);
  } else {
    externalSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_ROOT}${url}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
      signal: controller.signal,
    });
    return response;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      if (externalSignal?.aborted) throw error;
      throw new ApiUnavailableError("oh-goon backend did not respond in time", "BACKEND_TIMEOUT");
    }
    throw new ApiUnavailableError("oh-goon backend is unavailable");
  } finally {
    externalSignal?.removeEventListener("abort", abortFromCaller);
    window.clearTimeout(timeout);
  }
}

async function responseError(response: Response): Promise<ApiError> {
  const raw = await response.text();
  const payload = decodeTransportError(parseJson(raw));
  return new ApiError(
    payload.message,
    response.status,
    payload.code,
    payload.kind,
    payload.retryable ?? false,
    payload.details ?? {},
    payload.request_id ?? response.headers.get("x-request-id"),
  );
}

function parseJson(raw: string): unknown {
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    throw new ApiError("Backend returned invalid JSON", 0, "INVALID_API_CONTRACT", "INTERNAL");
  }
}

function delay(milliseconds: number, signal?: AbortSignal | null): Promise<void> {
  if (signal?.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    const abort = () => {
      window.clearTimeout(timeout);
      reject(abortError());
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted", "AbortError");
}
