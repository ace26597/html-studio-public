import { describe, expect, it } from "vitest";
import { changesPage, drawingHoldsReload, heldReloadNote, isPageFile, readAutoReload, shouldReloadPreview } from "./reload";

describe("preview reload rule", () => {
  it("reloads on file changes unless the page refreshes itself", () => {
    expect(shouldReloadPreview("files_changed", true)).toBe(true);
    expect(shouldReloadPreview("files_changed", false)).toBe(false);
  });

  it("treats a page that never reported auto_reload as reloadable", () => {
    expect(shouldReloadPreview("files_changed", undefined)).toBe(true);
    expect(readAutoReload({ type: "ready" })).toBe(true);
    expect(readAutoReload({ type: "ready", auto_reload: "no" })).toBe(true);
    expect(readAutoReload({ type: "ready", auto_reload: false })).toBe(false);
    expect(readAutoReload({ type: "ready", auto_reload: true })).toBe(true);
  });

  it("always reloads when the app comes up or goes away, after an inline edit and on restore", () => {
    for (const reason of ["app_running", "app_stopped", "inline_edit", "restore"] as const) {
      expect(shouldReloadPreview(reason, false)).toBe(true);
      expect(shouldReloadPreview(reason, true)).toBe(true);
      expect(shouldReloadPreview(reason, undefined)).toBe(true);
    }
  });
});

describe("a drawing holds reloads back", () => {
  it("while the person is drawing or holds marks that were not sent", () => {
    expect(drawingHoldsReload(true, 0)).toBe(true);
    expect(drawingHoldsReload(true, 3)).toBe(true);
    expect(drawingHoldsReload(false, 1)).toBe(true);
  });

  it("not once the drawing is sent or removed", () => {
    expect(drawingHoldsReload(false, 0)).toBe(false);
  });

  it("says what the page is waiting for", () => {
    expect(heldReloadNote(true, 2)).toMatch(/sent or removed/);
    expect(heldReloadNote(false, 1)).toMatch(/sent or removed/);
    expect(heldReloadNote(true, 0)).toMatch(/press Done/);
  });
});

describe("which changed files reload the page", () => {
  it("does not reload for the agent's working notes, research and saved source text", () => {
    expect(changesPage(["research/site-check.json"])).toBe(false);
    expect(changesPage(["notes/project.md", "research/notes/market.md"])).toBe(false);
    expect(changesPage(["sources/s3.txt", "sources/s3.pdf"])).toBe(false);
  });

  it("still reloads for page files, the source list a page may read, and the saved check", () => {
    expect(changesPage(["research/site-check.json", "index.html"])).toBe(true);
    expect(isPageFile("sources/index.json")).toBe(true);
    expect(isPageFile("checks/index.html.json")).toBe(true);     // the checked-numbers appendix shows it
    expect(isPageFile("attachments/photo.png")).toBe(true);      // a page may show an attached picture
    expect(isPageFile("research")).toBe(true);                    // a top-level file, not the folder
    expect(changesPage([])).toBe(true);
    expect(changesPage(undefined)).toBe(true);
  });
});
