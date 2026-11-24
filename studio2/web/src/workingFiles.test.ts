import { describe, expect, it } from "vitest";
import { isWorkingFile, splitWorking } from "./workingFiles";

const files = [
  { path: "index.html" },
  { path: "data/sales.csv" },
  { path: "files/diagrams/flow.svg" },
  { path: "sources/s1.txt" },
  { path: "checks/index.html.json" },
  { path: "research/notes.md" },
  { path: "attachments/pasted-text-20261124-153012.txt" },
  { path: "sources" },
];

describe("working files in the Files panel", () => {
  it("knows the studio's own folders, and leaves a file that only shares the name alone", () => {
    expect(isWorkingFile("sources/s1.txt")).toBe(true);
    expect(isWorkingFile("checks/a/b.json")).toBe(true);
    expect(isWorkingFile("attachments/photo.png")).toBe(true);
    expect(isWorkingFile("index.html")).toBe(false);
    expect(isWorkingFile("data/sources/list.csv")).toBe(false);
    expect(isWorkingFile("sources")).toBe(false);
    expect(isWorkingFile("sources-of-truth/a.md")).toBe(false);
  });

  it("hides them until asked, and always says how many there are", () => {
    const hidden = splitWorking(files, false);
    expect(hidden.shown.map((file) => file.path)).toEqual(["index.html", "data/sales.csv", "files/diagrams/flow.svg", "sources"]);
    expect(hidden.working).toBe(4);
    const all = splitWorking(files, true);
    expect(all.shown).toHaveLength(files.length);
    expect(all.working).toBe(4);
  });
});
