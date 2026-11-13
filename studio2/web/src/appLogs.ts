// The log drawer's buffer: pure merging of `GET …/app/logs?after=<n>` answers.

import type { AppLogLine, AppLogs } from "./types";

/** Lines kept in the browser; older ones are dropped (the server keeps 2,000 in memory). */
export const LOG_BUFFER = 5000;

export interface LogBuffer {
  lines: AppLogLine[];
  /** The `after` to ask with next. */
  after: number;
  /** True once lines had to be dropped from the front of the buffer. */
  trimmed: boolean;
}

export const emptyLogBuffer: LogBuffer = { lines: [], after: 0, trimmed: false };

/**
 * Merge one answer that was requested with `requestedAfter`.
 *
 * `restarted` is true when `last` is smaller than what was asked for: the app
 * started again and its line numbers began at 1, so the caller must clear the
 * buffer and ask again with `after=0` (the returned buffer is already empty).
 */
export function mergeLogs(
  buffer: LogBuffer,
  answer: AppLogs,
  requestedAfter: number,
  cap: number = LOG_BUFFER,
): { buffer: LogBuffer; restarted: boolean } {
  const last = typeof answer.last === "number" && Number.isFinite(answer.last) ? answer.last : 0;
  if (last < requestedAfter) return { buffer: emptyLogBuffer, restarted: true };
  // A late answer for an older cursor: keep only what is really new.
  const fresh = (Array.isArray(answer.lines) ? answer.lines : []).filter(
    (line) => line && typeof line.n === "number" && line.n > buffer.after,
  );
  let lines = fresh.length ? [...buffer.lines, ...fresh] : buffer.lines;
  let trimmed = buffer.trimmed;
  if (lines.length > cap) {
    lines = lines.slice(lines.length - cap);
    trimmed = true;
  }
  // With no lines but a higher `last` (older lines already left the server's
  // tail), move the cursor anyway so the next poll does not ask forever.
  const after = Math.max(buffer.after, fresh.length ? fresh[fresh.length - 1].n : 0, fresh.length ? 0 : last);
  if (lines === buffer.lines && after === buffer.after && trimmed === buffer.trimmed) {
    return { buffer, restarted: false };
  }
  return { buffer: { lines, after, trimmed }, restarted: false };
}
