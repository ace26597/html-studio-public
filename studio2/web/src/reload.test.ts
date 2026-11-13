import { describe, expect, it } from "vitest";
import { readAutoReload, shouldReloadPreview } from "./reload";

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
