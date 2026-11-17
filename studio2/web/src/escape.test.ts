import { describe, expect, it } from "vitest";
import { confirmEscape, escapeTarget, isTopmostModal } from "./escape";

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

describe("Escape under the confirm dialog", () => {
  const key = (name: string) => {
    const seen = { prevented: false, stopped: false };
    return {
      seen,
      event: { key: name, preventDefault: () => void (seen.prevented = true), stopPropagation: () => void (seen.stopped = true) },
    };
  };

  it("cancels it wherever focus is, and nothing else hears that Escape", () => {
    const dialog = { id: "confirm" };
    let cancelled = 0;
    const { event, seen } = key("Escape");
    confirmEscape(event, dialog, [{ id: "modal below" }, dialog], () => cancelled++);
    expect(cancelled).toBe(1);
    expect(seen).toEqual({ prevented: true, stopped: true });
  });

  it("leaves other keys, and a dialog opened above it, alone", () => {
    const dialog = { id: "confirm" };
    let cancelled = 0;
    const tab = key("Tab");
    confirmEscape(tab.event, dialog, [dialog], () => cancelled++);
    const covered = key("Escape");
    confirmEscape(covered.event, dialog, [dialog, { id: "above" }], () => cancelled++);
    expect(cancelled).toBe(0);
    expect(tab.seen.stopped || covered.seen.stopped).toBe(false);
  });
});
