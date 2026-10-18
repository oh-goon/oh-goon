import type { ErrorKind } from "@generated/models";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = "REQUEST_FAILED",
    readonly kind: ErrorKind = "INTERNAL",
    readonly retryable = false,
    readonly details: Record<string, unknown> = {},
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class ApiUnavailableError extends ApiError {
  constructor(message: string, code = "BACKEND_UNAVAILABLE") {
    super(message, 0, code, "INTERNAL", true);
    this.name = "ApiUnavailableError";
  }
}
