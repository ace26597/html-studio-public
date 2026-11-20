/** The desktop chat panel's size: dragged by its left edge, top edge or corner, and remembered in the browser. */

export interface ChatSize {
  /** Width in px. */
  w: number;
  /** Height in px, or null while the panel is as tall as its conversation (up to its usual limit). */
  h: number | null;
}

export const CHAT_MIN = { w: 360, h: 320 };
/** The header button's large size; the panel's own limits cut it to what the window has. */
export const CHAT_LARGE: ChatSize = { w: 680, h: 4000 };
/** The room the panel leaves around itself. */
export const CHAT_MARGIN = 40;
export type ResizeEdge = "left" | "top" | "corner";

const clamp = (value: number, low: number, high: number) => Math.round(Math.min(Math.max(value, low), Math.max(low, high)));

/**
 * The size while an edge is dragged. The panel sits at the bottom right, so dragging left makes it
 * wider and dragging up makes it taller. `start`: the pointer and the panel when the drag began;
 * `room`: the space the panel may fill; `before`: the size it had (for the side not being dragged).
 */
export function resized(
  start: { x: number; y: number; w: number; h: number },
  now: { x: number; y: number },
  edge: ResizeEdge,
  room: { w: number; h: number },
  before: ChatSize | null,
): ChatSize {
  const w = edge === "top" ? (before?.w ?? Math.round(start.w)) : clamp(start.w - (now.x - start.x), CHAT_MIN.w, room.w);
  const h = edge === "left" ? (before?.h ?? null) : clamp(start.h - (now.y - start.y), CHAT_MIN.h, room.h);
  return { w, h };
}

/** What is kept in the browser: "520x640", or "520" when only the width was set. */
export function formatChatSize(size: ChatSize | null): string | null {
  if (!size) return null;
  return size.h === null ? `${size.w}` : `${size.w}x${size.h}`;
}

export function parseChatSize(raw: string | null): ChatSize | null {
  const found = /^(\d{3,4})(?:x(\d{3,4}))?$/.exec(raw ?? "");
  if (!found) return null;
  const w = Number(found[1]);
  const h = found[2] ? Number(found[2]) : null;
  if (w < CHAT_MIN.w || (h !== null && h < CHAT_MIN.h)) return null;
  return { w, h };
}

/** The header button: what it does next and what it says. */
export function sizeToggle(large: boolean): { label: string; icon: "expand" | "shrink" } {
  return large ? { label: "Make the chat smaller", icon: "shrink" } : { label: "Make the chat larger", icon: "expand" };
}
