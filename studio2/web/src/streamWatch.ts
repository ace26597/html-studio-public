// Noticing a dead event stream. The server sends `event: ping` after 15 s of silence
// (CONTRACT.md, "Events"); a connection left half-open by a sleep or a change of network keeps
// saying "open" and delivers nothing, so silence is the only sign. Pure, so testable.

/** Three keep-alives missed in a row. */
export const STALL_MS = 45_000;

/** How often the page checks. */
export const STALL_CHECK_MS = 5_000;

export function streamStalled(lastHeardAt: number, now: number, limit = STALL_MS): boolean {
  return now - lastHeardAt >= limit;
}
