import { describe, expect, it } from "vitest";
import { cleanName, newPageSources } from "./NewProjectDialog";

describe("the optional name on New page", () => {
  it("sends the typed name tidied, and nothing when it was left empty", () => {
    expect(cleanName("  Garden   club site  ")).toBe("Garden club site");
    expect(cleanName("   ")).toBe("");
    expect(cleanName("")).toBe("");
  });

  it("never sends more than 80 characters", () => {
    expect(cleanName("a".repeat(200))).toHaveLength(80);
  });

  it("keeps the four ways to start, in order", () => {
    expect(newPageSources(true, true).map((item) => item.value)).toEqual(["template", "folder", "skill", "connector"]);
    expect(newPageSources(false, false).map((item) => item.value)).toEqual(["template", "folder"]);
  });

  it("offers no folder of this computer to a guest on a shared studio", () => {
    expect(newPageSources(false, false, false).map((item) => item.value)).toEqual(["template"]);
  });
});
