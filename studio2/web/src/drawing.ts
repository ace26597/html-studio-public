// Draw on the page: the shell's side. Pure helpers and the drawing state.

import type { IconName } from "./icons";
import type { Drawing, DrawTool } from "./types";

export const DRAW_COLORS: { value: string; label: string }[] = [
  { value: "#ef4444", label: "Red" },
  { value: "#f59e0b", label: "Amber" },
  { value: "#2563eb", label: "Blue" },
  { value: "#16a34a", label: "Green" },
];
export const DEFAULT_DRAW_COLOR = DRAW_COLORS[0].value;

export const DRAW_TOOLS: { id: DrawTool; label: string; icon: IconName; hint: string }[] = [
  { id: "pen", label: "Pen", icon: "pencil", hint: "Drag to draw." },
  { id: "box", label: "Box", icon: "square", hint: "Drag a box, then type what goes there." },
  { id: "note", label: "Note", icon: "note", hint: "Tap and type." },
  { id: "move", label: "Move", icon: "move", hint: "Drag something to where it should go." },
  { id: "pan", label: "Scroll", icon: "hand", hint: "Use the page as usual; your marks stay." },
];

/** What a message with a drawing says when the box is empty. */
export const DEFAULT_DRAWING_TEXT = "Make the changes I drew on the page.";

export interface DrawState {
  tool: DrawTool;
  color: string;
  /** Marks on the page now, as the frame last said. */
  count: number;
  /** The frame's latest Drawing, or null when there are no marks. */
  drawing: Drawing | null;
}

export const initialDraw: DrawState = { tool: "pen", color: DEFAULT_DRAW_COLOR, count: 0, drawing: null };

export type DrawAction =
  | { type: "tool"; tool: DrawTool }
  | { type: "color"; color: string }
  /** The frame's `drawing` message. */
  | { type: "frame-drawing"; count: number; drawing: Drawing | null }
  /** A page load (`ready`): the frame dropped its marks, so forget ours. */
  | { type: "frame-ready" }
  /** Sent, or removed by the person. */
  | { type: "forget" };

export function drawReducer(state: DrawState, action: DrawAction): DrawState {
  switch (action.type) {
    case "tool":
      return state.tool === action.tool ? state : { ...state, tool: action.tool };
    case "color":
      return state.color === action.color ? state : { ...state, color: action.color };
    case "frame-drawing": {
      const count = Number.isFinite(action.count) && action.count > 0 ? Math.floor(action.count) : 0;
      const drawing = count > 0 && action.drawing && Array.isArray(action.drawing.marks) ? action.drawing : null;
      return { ...state, count: drawing ? count : 0, drawing };
    }
    case "frame-ready":
    case "forget":
      return state.count === 0 && state.drawing === null ? state : { ...state, count: 0, drawing: null };
  }
}

export function toolHint(tool: DrawTool): string {
  return DRAW_TOOLS.find((item) => item.id === tool)?.hint ?? "";
}

/** The hint line under the toolbar names the tool, so icon-only buttons on a phone stay clear. */
export function toolLine(tool: DrawTool): string {
  const item = DRAW_TOOLS.find((entry) => entry.id === tool);
  return item ? `${item.label}: ${item.hint}` : "";
}

export function marksLabel(count: number): string {
  return `${count} ${count === 1 ? "mark" : "marks"}`;
}

/** The text a message is sent with: what was typed, or the default when only a drawing goes. */
export function messageText(typed: string, withDrawing: boolean): string {
  const text = typed.trim();
  if (text) return text;
  return withDrawing ? DEFAULT_DRAWING_TEXT : "";
}

/** The turn's attachments: the uploaded files, then the saved drawing (no duplicates). */
export function turnAttachments(files: readonly string[], drawingPath: string | null): string[] {
  const out: string[] = [];
  for (const path of [...files, ...(drawingPath ? [drawingPath] : [])]) if (path && !out.includes(path)) out.push(path);
  return out;
}

const DRAWING_FILE = /(^|\/)drawing-\d+\.json$/;

/** `attachments/drawing-<ms>.json` is a drawing, not a plain file. */
export function isDrawingAttachment(path: string): boolean {
  return DRAWING_FILE.test(path);
}

/** The region screenshots listed in a saved drawing file (`images`); [] when absent or unreadable. */
export function drawingImages(text: string): string[] {
  try {
    const data = JSON.parse(text) as { images?: unknown };
    if (!Array.isArray(data.images)) return [];
    // The saved file lists each image as {path, marks}; a plain path is accepted too.
    return data.images
      .map((item) => (typeof item === "string" ? item : (item as { path?: unknown } | null)?.path))
      .filter((item): item is string => typeof item === "string");
  } catch {
    return [];
  }
}
