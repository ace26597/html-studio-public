import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  CHECK_ONLY_OPTIONS,
  RECHECK_COVERS,
  fileProblemLine,
  recheckFrom,
  recheckHeadline,
  type VerificationItem,
} from "./effort";
import { initialThread, reduceEvents, type Turn } from "./thread";
import { RecheckNote } from "./ThreadView";
import type { StudioEvent } from "./types";

const ev = (seq: number, e: Record<string, unknown>) =>
  ({ seq, ts: `2026-11-13T09:00:0${seq}Z`, turn_id: "t_a", ...e }) as unknown as StudioEvent;

const report = {
  type: "verification", checked: 4, passed: 3, fixed: 1, failed: 0, unverifiable: 0, summary: "",
  findings: [{ what: "AAA source missing from source list (s27)", where: "Sources", status: "fixed", note: "" }],
};
const disagrees = {
  type: "verification_recheck",
  pages: [{ page: "index.html", checked: 4, verified: 2, derived: 1, failed: 1, cited_not_listed: 2, source_list: true, error: null }],
  problems: [
    { page: "index.html", what: "12%", where: "Margins", why: "The quote is not in the saved text of s1." },
    { page: "index.html", what: "Source s27", where: "", why: "Cited on the page but has no entry in the page's source list." },
    { page: "index.html", what: "Source s2", where: "", why: "Cited on the page but has no entry in the page's source list." },
  ],
  problems_total: 3, problems_cut: 0, agrees: false,
};

describe("the studio's own re-check", () => {
  it("joins the request's last card", () => {
    const state = reduceEvents(initialThread, [
      ev(1, { type: "turn_started", text: "Verify content", options: CHECK_ONLY_OPTIONS }),
      ev(2, report),
      ev(3, disagrees),
    ]);
    const turn = state.entries.find((e) => "items" in e) as Turn;
    const cards = turn.items.filter((i) => i.type === "verification") as VerificationItem[];
    expect(cards).toHaveLength(1);
    expect(cards[0].recheck?.total).toBe(3);
    expect(cards[0].recheck?.problems.map((p) => p.what)).toEqual(["12%", "Source s27", "Source s2"]);
  });

  it("is ignored when there is no card to correct", () => {
    const state = reduceEvents(initialThread, [ev(1, { type: "turn_started", text: "x" }), ev(2, disagrees)]);
    const turn = state.entries.find((e) => "items" in e) as Turn;
    expect(turn.items.some((i) => i.type === "verification")).toBe(false);
  });

  it("says what is still wrong, or that it agrees, or that it saw nothing to compare", () => {
    expect(recheckHeadline(recheckFrom(disagrees))).toBe("3 items the studio’s own check still finds wrong");
    const one = recheckFrom({ ...disagrees, problems: disagrees.problems.slice(0, 1), problems_total: 1 });
    expect(recheckHeadline(one)).toBe("1 item the studio’s own check still finds wrong");
    const ok = recheckFrom({ ...disagrees, problems: [], problems_total: 0, agrees: true });
    expect(recheckHeadline(ok)).toMatch(/agrees/);
    const empty = recheckFrom({ pages: [{ page: "index.html", checked: 0, error: null }], problems: [], agrees: true });
    expect(recheckHeadline(empty)).toMatch(/no marked figures/);
    const unread = recheckFrom({ pages: [{ page: "index.html", checked: 0, error: "There is no page" }], problems: [] });
    expect(recheckHeadline(unread)).toMatch(/could not read/);
  });

  it("lists the problems first and says what the re-check covers", () => {
    const html = renderToStaticMarkup(<RecheckNote recheck={recheckFrom(disagrees)} />);
    expect(html).toContain("3 items the studio’s own check still finds wrong");
    expect(html.indexOf("12%")).toBeLessThan(html.indexOf("Source s27"));
    expect(html).toContain("no entry in the page");
    expect(html).toContain(RECHECK_COVERS.replace(/’/g, "’"));
    const agreed = renderToStaticMarkup(
      <RecheckNote recheck={recheckFrom({ ...disagrees, problems: [], problems_total: 0, agrees: true })} />,
    );
    expect(agreed).not.toContain("<ul");
    expect(agreed).toMatch(/agrees/);
    expect(agreed).toContain("covers only what its checks can see");
    expect(agreed).toContain("Office and PDF files");
  });

  it("never reads as all checked when pages or files were left out", () => {
    const skipped = recheckFrom({
      ...disagrees, problems: [], problems_total: 0, agrees: false, not_rechecked: ["files/a.pptx", "files/b.docx", 3],
    });
    expect(skipped.notRechecked).toEqual(["files/a.pptx", "files/b.docx"]);
    expect(recheckHeadline(skipped)).toBe(
      "The studio’s own check found nothing wrong in what it read, but 2 more pages or files were not re-checked.",
    );
    const html = renderToStaticMarkup(<RecheckNote recheck={skipped} />);
    expect(html).toContain("Not re-checked (the re-check reads at most 10 pages and 10 files): files/a.pptx, files/b.docx");
    expect(recheckFrom(disagrees).notRechecked).toEqual([]);
  });

  it("the check button sends a check-only request", () => {
    expect(CHECK_ONLY_OPTIONS).toEqual({ depth: "standard", verify: true, check_only: true });
  });

  it("words a problem in a file by its place, and names the file when there is more than one", () => {
    const withFiles = recheckFrom({
      pages: [
        { page: "index.html", kind: "page", checked: 4, error: null },
        { page: "files/deck.pptx", kind: "file", checked: 3, error: null },
      ],
      problems: [
        { page: "index.html", kind: "page", what: "12%", where: "Margins", why: "The quote is not in the saved text of s1." },
        { page: "files/deck.pptx", kind: "file", what: "$4.3 billion", where: "Slide 3, title", why: "Not in any checked page figure, data file or saved source." },
      ],
      problems_total: 2,
    });
    const [page, file] = withFiles.problems;
    expect(page.kind).toBe("page");
    expect(file.kind).toBe("file");
    expect(fileProblemLine(file, false)).toBe("Slide 3, title: $4.3 billion");
    expect(fileProblemLine(file, true)).toBe("deck.pptx · Slide 3, title: $4.3 billion");
    expect(fileProblemLine({ ...file, where: "" }, false)).toBe("$4.3 billion");
    const html = renderToStaticMarkup(<RecheckNote recheck={withFiles} />);
    expect(html).toContain("deck.pptx · Slide 3, title: $4.3 billion");
    expect(html).toContain("Not in any checked page figure");
    expect(html.indexOf("12%")).toBeLessThan(html.indexOf("deck.pptx"));
    const onlyFile = recheckFrom({ pages: [withFiles.pages[1]], problems: [withFiles.problems[1]], problems_total: 1 });
    expect(renderToStaticMarkup(<RecheckNote recheck={onlyFile} />)).toContain(">Slide 3, title: $4.3 billion<");
    // An event from before files were checked reads as pages.
    expect(recheckFrom(disagrees).pages[0].kind).toBe("page");
    expect(recheckFrom(disagrees).problems[0].kind).toBe("page");
  });

  it("says when it saw nothing in the page or its files", () => {
    const nothing = recheckFrom({
      pages: [
        { page: "index.html", kind: "page", checked: 0, error: null },
        { page: "deck.pptx", kind: "file", checked: 0, error: null },
      ],
      problems: [],
      agrees: true,
    });
    expect(recheckHeadline(nothing)).toMatch(/no marked figures on the page and no figures in its files/);
    const unread = recheckFrom({ pages: [{ page: "deck.pptx", kind: "file", checked: 0, error: "broken" }], problems: [] });
    expect(recheckHeadline(unread)).toMatch(/could not read the page or its files/);
  });
});
