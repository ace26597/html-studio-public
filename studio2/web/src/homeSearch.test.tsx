// The home page's search (CONTRACT.md, "Find words in a project's pages and across projects").
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FindResults, HomeSearchBox, searchStatus, type FindState } from "./HomeFind";
import { fold, highlight, matchesAll, queryTerms } from "./homeSearch";
import { parseRoute, projectHref, projectParams } from "./router";
import type { StudioFind } from "./types";

describe("matching, as on the server", () => {
  it("folds accents, case and white space", () => {
    expect(fold("  Café\n\t ÉLAN  naïve ")).toBe("cafe elan naive");
  });

  it("splits words and keeps quoted phrases", () => {
    expect(queryTerms('garden "trip budget" Plan').sort()).toEqual(["garden", "plan", "trip budget"]);
    expect(queryTerms("“trip budget”")).toEqual(["trip budget"]);
    expect(queryTerms('note "trip bud')).toEqual(["trip bud", "note"]);
    for (const nothing of ["", " ", "a", '"a"', "é"]) expect(queryTerms(nothing)).toEqual([]);
  });

  it("needs every word in the text", () => {
    expect(matchesAll("The Garden plan", queryTerms("gard plan"))).toBe(true);
    expect(matchesAll("The Garden", queryTerms("gard plan"))).toBe(false);
    expect(matchesAll("Le Café", queryTerms("cafe"))).toBe(true);
    expect(matchesAll("anything", [])).toBe(false);
  });

  it("marks every match in the original text, accents kept", () => {
    expect(highlight("Un café, deux CAFÉS", ["cafe"])).toEqual([
      { text: "Un ", match: false },
      { text: "café", match: true },
      { text: ", deux ", match: false },
      { text: "CAFÉ", match: true },
      { text: "S", match: false },
    ]);
    expect(highlight("garden plan", ["garden plan", "plan"])).toEqual([{ text: "garden plan", match: true }]);
    expect(highlight("nothing", ["zz"])).toEqual([{ text: "nothing", match: false }]);
  });
});

describe("the project address", () => {
  it("carries the page and the words, and still opens the project", () => {
    const href = projectHref("p_1234abcd", { page: "notes/a b.md", find: "garden plan" });
    expect(href).toBe("/p/p_1234abcd?page=notes%2Fa+b.md&find=garden+plan");
    expect(parseRoute(href)).toEqual({ name: "project", id: "p_1234abcd" });
    expect(projectParams(href.slice(href.indexOf("?")))).toEqual({ page: "notes/a b.md", find: "garden plan" });
    expect(projectHref("p_1234abcd")).toBe("/p/p_1234abcd");
    expect(projectHref("p_1234abcd", { find: "  " })).toBe("/p/p_1234abcd");
    expect(projectParams("")).toEqual({ page: null, find: null });
  });
});

const RESULT: StudioFind = {
  query: "garden",
  capped: true,
  projects: [
    { id: "p_aaaa1111", name: "Garden plan", name_match: true, attached: false, pages: [], chat: [], more: { pages: 0, chat: 0 } },
    {
      id: "p_bbbb2222",
      name: "Trip budget",
      name_match: false,
      attached: false,
      pages: [
        {
          path: "notes/stops.md",
          title: "Stops",
          hits: [{ kind: "text", section: "Day two", snippet: "A garden on the way" }],
          more: 2,
        },
      ],
      chat: [{ seq: 7, role: "person", snippet: "Add the garden stop", at: "2026-11-01T10:00:00Z" }],
      more: { pages: 1, chat: 0 },
    },
  ],
};

describe("what is found inside the pages", () => {
  it("lists projects with page and chat hits, linked, with the words bold", () => {
    const html = renderToStaticMarkup(<FindResults state={{ status: "done", query: "garden", result: RESULT }} onGo={() => {}} />);
    expect(html).not.toContain("p_aaaa1111");           // a name-only match is a card, not listed again
    expect(html).toContain('href="/p/p_bbbb2222?page=notes%2Fstops.md&amp;find=garden"');
    expect(html).toContain('href="/p/p_bbbb2222?find=garden"');
    expect(html).toContain("Day two");
    expect(html).toContain("<span>A </span><mark>garden</mark>");
    expect(html).toContain("and 2 more in this file");
    expect(html).toContain("Also 1 more file");
    expect(html).toContain(">You<");
    expect(html).toContain("Not everything was searched");
  });

  it("says when it is searching or failed, and nothing when idle", () => {
    expect(renderToStaticMarkup(<FindResults state={{ status: "idle" }} onGo={() => {}} />)).toBe("");
    expect(renderToStaticMarkup(<FindResults state={{ status: "searching", query: "garden" }} onGo={() => {}} />)).toContain(
      "Searching",
    );
    expect(
      renderToStaticMarkup(<FindResults state={{ status: "failed", query: "garden", message: "No answer." }} onGo={() => {}} />),
    ).toContain("No answer.");
  });

  it("the box says nothing found plainly", () => {
    const none: FindState = { status: "done", query: "zz", result: { query: "zz", projects: [], capped: false } };
    expect(searchStatus("", none, 0)).toBeNull();
    expect(searchStatus("a", none, 0)).toBe("Type at least two letters");
    expect(searchStatus("zz", none, 0)).toBe("Nothing found");
    expect(searchStatus("zz", none, 2)).toBeNull();
    expect(searchStatus("zz", { status: "searching", query: "zz" }, 0)).toBe("Searching…");
    const html = renderToStaticMarkup(<HomeSearchBox value="zz" onChange={() => {}} status="Nothing found" />);
    expect(html).toContain('type="search"');
    expect(html).toContain("Nothing found");
  });
});
