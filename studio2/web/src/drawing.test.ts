import { describe, expect, it } from "vitest";
import {
  DEFAULT_DRAW_COLOR,
  DRAW_TOOLS,
  DEFAULT_DRAWING_TEXT,
  drawingImages,
  drawReducer,
  initialDraw,
  isDrawingAttachment,
  marksLabel,
  messageText,
  toolHint,
  toolLine,
  turnAttachments,
} from "./drawing";
import type { Drawing } from "./types";

// Shape only, for the rules under test.
const drawing = (n: number): Drawing => ({
  path: "/",
  viewport: { width: 390, height: 760 },
  marks: Array.from({ length: n }, (_, i) => ({ n: i + 1, kind: "stroke" })),
});

describe("drawReducer", () => {
  it("starts with the pen in red and nothing drawn", () => {
    expect(initialDraw).toEqual({ tool: "pen", color: DEFAULT_DRAW_COLOR, count: 0, drawing: null });
    expect(DEFAULT_DRAW_COLOR).toBe("#ef4444");
  });

  it("keeps the frame's latest drawing and count", () => {
    const one = drawReducer(initialDraw, { type: "frame-drawing", count: 1, drawing: drawing(1) });
    const three = drawReducer(one, { type: "frame-drawing", count: 3, drawing: drawing(3) });
    expect(three.count).toBe(3);
    expect(three.drawing?.marks).toHaveLength(3);
  });

  it("forgets the drawing when the frame says there are no marks", () => {
    const some = drawReducer(initialDraw, { type: "frame-drawing", count: 2, drawing: drawing(2) });
    expect(drawReducer(some, { type: "frame-drawing", count: 0, drawing: null })).toMatchObject({ count: 0, drawing: null });
    // A count without a drawing is not something to send.
    expect(drawReducer(initialDraw, { type: "frame-drawing", count: 2, drawing: null }).count).toBe(0);
  });

  it("forgets the drawing on a page load and when sent or removed, keeping tool and colour", () => {
    const state = drawReducer(
      drawReducer(drawReducer(initialDraw, { type: "tool", tool: "note" }), { type: "color", color: "#2563eb" }),
      { type: "frame-drawing", count: 2, drawing: drawing(2) },
    );
    for (const type of ["frame-ready", "forget"] as const) {
      expect(drawReducer(state, { type })).toEqual({ tool: "note", color: "#2563eb", count: 0, drawing: null });
    }
    expect(drawReducer(initialDraw, { type: "forget" })).toBe(initialDraw);
  });

  it("changes tool and colour", () => {
    for (const tool of ["box", "note", "move", "pan"] as const) {
      expect(drawReducer(initialDraw, { type: "tool", tool }).tool).toBe(tool);
    }
    expect(drawReducer(initialDraw, { type: "tool", tool: "pen" })).toBe(initialDraw);
    expect(drawReducer(initialDraw, { type: "color", color: "#16a34a" }).color).toBe("#16a34a");
  });
});

describe("sending a drawing", () => {
  it("uses the typed text, or a plain default when only a drawing goes", () => {
    expect(messageText("  make it blue ", true)).toBe("make it blue");
    expect(messageText("   ", true)).toBe(DEFAULT_DRAWING_TEXT);
    expect(messageText("", false)).toBe("");
  });

  it("adds the saved drawing after the uploaded files, once", () => {
    expect(turnAttachments(["attachments/a.png"], "attachments/drawing-1.json")).toEqual([
      "attachments/a.png",
      "attachments/drawing-1.json",
    ]);
    expect(turnAttachments([], null)).toEqual([]);
    expect(turnAttachments(["attachments/drawing-1.json"], "attachments/drawing-1.json")).toEqual([
      "attachments/drawing-1.json",
    ]);
  });
});

describe("drawings in the thread", () => {
  it("recognises a drawing file but not its images or other json", () => {
    expect(isDrawingAttachment("attachments/drawing-1731234567890.json")).toBe(true);
    expect(isDrawingAttachment("attachments/drawing-1731234567890-1.jpg")).toBe(false);
    expect(isDrawingAttachment("data/drawing-notes.json")).toBe(false);
    expect(isDrawingAttachment("attachments/data.json")).toBe(false);
  });

  it("reads the region images from a saved drawing", () => {
    expect(drawingImages(JSON.stringify({ images: ["attachments/drawing-1-1.jpg", 4] }))).toEqual([
      "attachments/drawing-1-1.jpg",
    ]);
    expect(
      drawingImages(JSON.stringify({ images: [{ path: "attachments/drawing-1-1.jpg", marks: [1, 2] }, { marks: [3] }] })),
    ).toEqual(["attachments/drawing-1-1.jpg"]);
    expect(drawingImages("{}")).toEqual([]);
    expect(drawingImages("not json")).toEqual([]);
  });

  it("words counts and hints plainly", () => {
    expect(marksLabel(1)).toBe("1 mark");
    expect(marksLabel(3)).toBe("3 marks");
    expect(toolHint("pen")).toBe("Drag to draw.");
    expect(toolHint("note")).toBe("Tap and type.");
    expect(toolHint("box")).toBe("Drag a box, then type what goes there.");
    expect(toolHint("move")).toBe("Drag something to where it should go.");
    expect(toolHint("pan")).toBe("Use the page as usual; your marks stay.");
    expect(toolLine("pan")).toBe("Scroll: Use the page as usual; your marks stay.");
  });

  it("offers the five tools in order: Pen, Box, Note, Move, Scroll", () => {
    expect(DRAW_TOOLS.map((tool) => [tool.id, tool.label])).toEqual([
      ["pen", "Pen"],
      ["box", "Box"],
      ["note", "Note"],
      ["move", "Move"],
      ["pan", "Scroll"],
    ]);
  });
});
