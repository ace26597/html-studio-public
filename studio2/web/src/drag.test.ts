import { describe, expect, it } from "vitest";
import { DRAG_IDLE_MS, dragExpired, isFileDragMessage } from "./drag";

describe("page drop target", () => {
  it("expires only after the idle time without drag events", () => {
    expect(dragExpired(1000, 1000 + DRAG_IDLE_MS)).toBe(false);
    expect(dragExpired(1000, 1001 + DRAG_IDLE_MS)).toBe(true);
    expect(dragExpired(0, 50, 100)).toBe(false);
  });

  it("recognises the bridge's file-drag message and nothing else", () => {
    expect(isFileDragMessage({ source: "studio-bridge", type: "file-drag" })).toBe(true);
    expect(isFileDragMessage({ source: "studio-shell", type: "file-drag" })).toBe(false);
    expect(isFileDragMessage({ source: "studio-bridge", type: "ready" })).toBe(false);
    expect(isFileDragMessage(null)).toBe(false);
    expect(isFileDragMessage("file-drag")).toBe(false);
  });
});
