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

let sent = false;
let waiting = false;
let hold: (() => boolean) | null = null;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((listener) => listener());

/**
 * A page that holds something unsaved (the file viewer's edits) and must not be left for the
 * sign-in page. Returns a clean-up that releases it only if it is still this page's.
 */
export function holdSignIn(check: () => boolean): () => void {
  hold = check;
  return () => {
    if (hold === check) hold = null;
  };
}

/** True while the studio needs signing in again and the tab stays, to keep unsaved edits. */
export function signInWaiting(): boolean {
  return waiting;
}

export function onSignInChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Once per page: several calls answered 401 at the same moment send the browser one time. While
 * unsaved edits are held, the tab stays and waits instead (CONTRACT.md, "Access from other devices").
 */
export function goToSignIn(): void {
  if (sent || window.location.pathname === "/login") return;
  if (hold?.()) {
    if (!waiting) {
      waiting = true;
      changed();
    }
    return;
  }
  sent = true;
  window.location.assign(signInUrl(window.location.href));
}

/** A call was answered again (the person signed in in another tab): stop waiting. */
export function signedInAgain(): void {
  if (!waiting) return;
  waiting = false;
  changed();
}

export function resetSignInForTests(): void {
  sent = false;
  waiting = false;
  hold = null;
  listeners.clear();
}
