// What a failed read of the project record does to the project page. Pure, so the rule is testable.

import { ApiError, errorMessage } from "./api";

export interface LoadError {
  missing: boolean;
  message: string;
}

/** How long a refresh that failed waits before it tries again. */
export const REFRESH_RETRY_MS = 5000;

/**
 * The error screen replaces the whole page, so it is only for a page that has nothing to show
 * yet (the first load) or whose project is gone (404). A later refresh that fails, from a
 * network blip as a request ends, keeps the page, the typed draft and the attachments; the
 * caller tries again, and a dropped stream already shows "Reconnecting".
 */
export function refreshFailure(hasProject: boolean, error: unknown): LoadError | null {
  const missing = error instanceof ApiError && error.status === 404;
  if (hasProject && !missing) return null;
  return { missing, message: errorMessage(error) };
}
