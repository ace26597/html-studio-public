import { describe, expect, it, vi } from "vitest";
import { cancelStray, carriesFiles, guardStrayDrops } from "./drag";

function drag(types: string[], handled = false) {
  return { defaultPrevented: handled, dataTransfer: { types, dropEffect: "copy" }, preventDefault: vi.fn() };
}

describe("a file dropped outside the chat", () => {
  it("knows a drag that carries files", () => {
    expect(carriesFiles(["Files"])).toBe(true);
    expect(carriesFiles(["text/uri-list", "text/html", "Files"])).toBe(true);
    expect(carriesFiles(["text/uri-list", "text/html"])).toBe(false);
    expect(carriesFiles(null)).toBe(false);
  });

  it("is cancelled, and shown as not allowed while over the studio", () => {
    const over = drag(["Files"]);
    expect(cancelStray(over, true)).toBe(true);
    expect(over.preventDefault).toHaveBeenCalledOnce();
    expect(over.dataTransfer.dropEffect).toBe("none");
    const drop = drag(["Files"]);
    expect(cancelStray(drop, false)).toBe(true);
    expect(drop.dataTransfer.dropEffect).toBe("copy");
  });

  it("leaves alone a drop the chat or the Files panel took, and a drag of text", () => {
    const taken = drag(["Files"], true);
    expect(cancelStray(taken, true)).toBe(false);
    expect(taken.preventDefault).not.toHaveBeenCalled();
    expect(taken.dataTransfer.dropEffect).toBe("copy");
    const text = drag(["text/plain"]);
    expect(cancelStray(text, false)).toBe(false);
    expect(text.preventDefault).not.toHaveBeenCalled();
  });

  it("listens on the window and stops when asked", () => {
    const target = { addEventListener: vi.fn(), removeEventListener: vi.fn() };
    const stop = guardStrayDrops(target);
    expect(target.addEventListener.mock.calls.map((call) => call[0])).toEqual(["dragover", "drop"]);
    stop();
    expect(target.removeEventListener.mock.calls.map((call) => call[0])).toEqual(["dragover", "drop"]);
  });
});
