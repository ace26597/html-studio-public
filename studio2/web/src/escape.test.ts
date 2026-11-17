import { describe, expect, it } from "vitest";
import { escapeTarget, isTopmostModal } from "./escape";

const closed = { modalOpen: false, versionSelected: false, rightPanel: false, chatOpen: false, filesOpen: false };

describe("what one Escape closes on the project page", () => {
  it("nothing of the page while a dialog is open: the dialog closes itself", () => {
    expect(escapeTarget({ ...closed, modalOpen: true, chatOpen: true, filesOpen: true, rightPanel: true })).toBe("none");
  });

  it("otherwise one thing, the most recently relevant", () => {
    expect(escapeTarget({ ...closed, versionSelected: true, rightPanel: true, chatOpen: true })).toBe("version");
    expect(escapeTarget({ ...closed, rightPanel: true, chatOpen: true })).toBe("right");
    expect(escapeTarget({ ...closed, chatOpen: true, filesOpen: true })).toBe("chat");
    expect(escapeTarget({ ...closed, filesOpen: true })).toBe("files");
    expect(escapeTarget(closed)).toBe("none");
  });
});

describe("the topmost dialog", () => {
  it("is the last one in the document", () => {
    const below = { id: "below" };
    const above = { id: "above" };
    expect(isTopmostModal(above, [below, above])).toBe(true);
    expect(isTopmostModal(below, [below, above])).toBe(false);
    expect(isTopmostModal(below, [below])).toBe(true);
    expect(isTopmostModal(null, [below])).toBe(false);
  });
});
