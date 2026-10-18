const BEARER_TOKEN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const AUTHORITY_SECRET = /([?&](?:token|key|secret|password|authorization)=)[^&#\s]+/gi;
const WINDOWS_USER_PATH = /\b[A-Za-z]:\\Users\\[^\\\s]+\\[^\s]*/g;
const UNIX_USER_PATH = /\/(?:home|Users)\/[^/\s]+\/[^\s]*/g;

export function redactDiagnosticText(value: string): string {
  return value
    .replace(BEARER_TOKEN, "Bearer [REDACTED]")
    .replace(AUTHORITY_SECRET, "$1[REDACTED]")
    .replace(WINDOWS_USER_PATH, "[LOCAL_PATH]")
    .replace(UNIX_USER_PATH, "[LOCAL_PATH]");
}
