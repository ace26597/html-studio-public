import { describe, expect, it, vi } from "vitest";
import { coreOf, fileOfPath, isWebPage, otherPageRows, otherPagesNote } from "./otherPages";
import type { ProjectFind } from "./types";

const result: ProjectFind = {
  query: "tomato",
  attached: false,
  capped: false,
  pages: [
    { path: "index.html", title: "Garden plan", more: 0, hits: [{ kind: "heading", section: null, snippet: "Tomato beds" }] },
    {
      path: "pages/costs.html",
      title: "Costs",
      more: 2,
      hits: [
        { kind: "text", section: "Seeds", snippet: "…we buy tomato seeds in March and plant them out when the frost is over…" },
        { kind: "text", section: null, snippet: "Tomato cages" },
      ],
    },
    { path: "notes/project.md", title: null, more: 0, hits: [{ kind: "text", section: "Next", snippet: "Order tomato seeds" }] },
  ],
};

describe("the file a preview path shows", () => {
  it("reads a folder as its index page and drops the query", () => {
    expect(fileOfPath("/")).toBe("index.html");
    expect(fileOfPath("/pages/")).toBe("pages/index.html");
    expect(fileOfPath("/pages/costs.html?x=1#top")).toBe("pages/costs.html");
    expect(fileOfPath("/my%20page.html")).toBe("my page.html");
    expect(fileOfPath("/bad%zz.html")).toBe("bad%zz.html");
  });
  it("knows a web page from a note", () => {
    expect(isWebPage("a/b.HTML")).toBe(true);
    expect(isWebPage("a/b.htm")).toBe(true);
    expect(isWebPage("notes/project.md")).toBe(false);
  });
});

describe("the words looked for again on the other page", () => {
  it("keeps the match and a little on each side, without the cut marks", () => {
    const core = coreOf("…we buy tomato seeds in March and plant them out…", "tomato");
    expect(core).toContain("tomato");
    expect(core).not.toContain("…");
    expect(core.length).toBeLessThanOrEqual("tomato".length + 30);
    expect("we buy tomato seeds in March and plant them out").toContain(core);
  });
  it("falls back to the start when the snippet holds no match", () => {
    expect(coreOf("Nothing of the kind here at all, really", "tomato")).toBe("Nothing of the kind here at al");
  });
});

describe("rows for the other pages", () => {
  it("leaves out the page shown and names the page in each row", () => {
    const rows = otherPageRows(result, "/", "tomato", { page: vi.fn(), file: vi.fn() });
    expect(rows.map((row) => row.key)).toEqual(["other:pages/costs.html:0", "other:pages/costs.html:1", "other:notes/project.md:0"]);
    expect(rows[0].section).toBe("Costs (pages/costs.html) · Seeds");
    expect(rows[1].section).toBe("Costs (pages/costs.html)");
    expect(rows[2].section).toBe("notes/project.md · Next");
  });
  it("shows a web page and goes to the match; opens a note as a file", () => {
    const go = { page: vi.fn(), file: vi.fn() };
    const rows = otherPageRows(result, "/index.html", "tomato", go);
    rows[0].onGo();
    expect(go.page).toHaveBeenCalledWith("pages/costs.html", expect.stringContaining("tomato"), "other:pages/costs.html:0");
    rows[2].onGo();
    expect(go.file).toHaveBeenCalledWith("notes/project.md", "other:notes/project.md:0");
  });
  it("lists the first page again once another page is shown", () => {
    const rows = otherPageRows(result, "/pages/costs.html", "tomato", { page: vi.fn(), file: vi.fn() });
    expect(rows.map((row) => row.key)).toEqual(["other:index.html:0", "other:notes/project.md:0"]);
  });
});

describe("what the group says", () => {
  it("says when there is more, when nothing was found, and why an attached app has none", () => {
    expect(otherPagesNote(result, false, 3, "/")).toBe("And 2 more: add a word to narrow it down.");
    expect(otherPagesNote(result, false, 2, "/pages/costs.html")).toBeNull();
    expect(otherPagesNote({ ...result, pages: [] }, false, 0, "/")).toBe("Nothing in the other pages.");
    expect(otherPagesNote({ ...result, pages: [], attached: true }, false, 0, "/")).toMatch(/attached app/);
    expect(otherPagesNote({ ...result, capped: true }, false, 3, "/")).toMatch(/Not everything/);
    expect(otherPagesNote(null, true, 0, "/")).toMatch(/could not be searched/);
    expect(otherPagesNote(null, false, 0, "/")).toBeNull();
  });
});
