// The page-wide drop target. Pure parts, so the rules are testable.

/** With no dragenter/dragover for this long, a shown drop target is treated as a cancelled drag. */
export const DRAG_IDLE_MS = 2000;

/** True when the drop target has seen no drag activity for `idleMs` (a drag cancelled where the shell can't see it). */
export function dragExpired(lastActivityAt: number, now: number, idleMs: number = DRAG_IDLE_MS): boolean {
  return now - lastActivityAt > idleMs;
}

/** The bridge's `file-drag`: a drag carrying files entered the page inside the frame. */
export function isFileDragMessage(data: unknown): boolean {
  return Boolean(
    data &&
      typeof data === "object" &&
      (data as Record<string, unknown>).source === "studio-bridge" &&
      (data as Record<string, unknown>).type === "file-drag",
  );
}
