import { describe, expect, it } from "vitest";
import { focusTarget, nextFocusIndex, tabKeepsInside } from "./dialogFocus";

// A fake dialog: `querySelector` answers from a list of selectors it holds a match for.
const root = (matches: Record<string, string>) => ({
  id: "dialog",
  querySelector: (selector: string) => {
    for (const [key, value] of Object.entries(matches)) if (selector.includes(key)) return { id: value };
    return null;
  },
});

describe("where focus goes when a dialog opens", () => {
  it("is the field marked for it, else the first control that is not the close button", () => {
    expect(focusTarget(root({ "[data-autofocus]": "name", "button:not": "first" }))).toEqual({ id: "name" });
    expect(focusTarget(root({ "button:not": "first" }))).toEqual({ id: "first" });
  });

  it("is the dialog itself when it has nothing else yet (Export while its ways load)", () => {
    const dialog = root({});
    expect(focusTarget(dialog)).toBe(dialog);
  });
});

describe("Tab and Shift+Tab stay inside the dialog", () => {
  it("go round the controls", () => {
    expect(nextFocusIndex(3, 0, false)).toBe(1);
    expect(nextFocusIndex(3, 2, false)).toBe(0);
    expect(nextFocusIndex(3, 0, true)).toBe(2);
  });

  it("from the dialog itself or from outside it, go to the first or the last control", () => {
    expect(nextFocusIndex(3, -1, false)).toBe(0);
    expect(nextFocusIndex(3, -1, true)).toBe(2);
    expect(nextFocusIndex(1, -1, true)).toBe(0);
  });

  it("are handled only for the topmost dialog, and only for Tab", () => {
    const a = { id: "a" };
    const b = { id: "b" };
    expect(tabKeepsInside("Tab", a, [a])).toBe(true);
    expect(tabKeepsInside("Tab", a, [a, b])).toBe(false);
    expect(tabKeepsInside("Enter", a, [a])).toBe(false);
  });
});
