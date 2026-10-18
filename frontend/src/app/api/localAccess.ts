import { requestLocalSession } from "@/app/api/httpTransport";

const LOCAL_ACCESS_FRAGMENT = "access=";
export type LocalAccessResult =
  | { readonly status: "ready" }
  | { readonly status: "required" };

export async function ensureLocalAccess(): Promise<LocalAccessResult> {
  const token = launchAccessToken();
  if (token !== null) {
    clearLaunchAccessToken();
    const accepted = await requestLocalSession(token);
    if (!accepted) return { status: "required" };
    return { status: "ready" };
  }

  const accepted = await requestLocalSession();
  return accepted ? { status: "ready" } : { status: "required" };
}

function launchAccessToken(): string | null {
  const fragment = window.location.hash.slice(1);
  if (!fragment.startsWith(LOCAL_ACCESS_FRAGMENT)) return null;
  const token = fragment.slice(LOCAL_ACCESS_FRAGMENT.length);
  return token.length > 0 ? token : null;
}

function clearLaunchAccessToken(): void {
  window.history.replaceState(
    window.history.state,
    "",
    `${window.location.pathname}${window.location.search}`,
  );
}
