// A studio opened from another device needs its access cookie (CONTRACT.md, "Access from other
// devices"). When the cookie has expired, or the key was changed, every `/api/*` call answers 401:
// that is a sign-in, not a studio that is down. Pure apart from `goToSignIn`.

import { ApiError, errorMessage } from "./api";

export type HealthFailure = { kind: "sign-in" } | { kind: "down" } | { kind: "error"; message: string };

/** Statuses a proxy in front of the studio (Vite in development, a tunnel) answers with when it is not there. */
const NOT_THERE = new Set([0, 502, 503, 504]);

export function needsSignIn(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export function healthFailure(error: unknown): HealthFailure {
  if (needsSignIn(error)) return { kind: "sign-in" };
  if (!(error instanceof ApiError) || NOT_THERE.has(error.status)) return { kind: "down" };
  return { kind: "error", message: errorMessage(error) };
}

/** The studio's own sign-in page, which sends the person back to `href` afterwards. */
export function signInUrl(href: string): string {
  return `/login?next=${encodeURIComponent(href)}`;
}

export function goToSignIn(): void {
  if (window.location.pathname === "/login") return;
  window.location.assign(signInUrl(window.location.href));
}
