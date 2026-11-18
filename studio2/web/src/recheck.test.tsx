import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CHECK_ONLY_OPTIONS, RECHECK_COVERS, recheckFrom, recheckHeadline, type VerificationItem } from "./effort";
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
    expect(agreed).toContain("covers only what the page check can see");
  });

  it("the check button sends a check-only request", () => {
    expect(CHECK_ONLY_OPTIONS).toEqual({ depth: "standard", verify: true, check_only: true });
  });
});
