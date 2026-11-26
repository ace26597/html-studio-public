import bridgeText from "../../server/studio2_server/static/bridge.js?raw";
import { describe, expect, it } from "vitest";
import {
  chatKey,
  flatRows,
  fold,
  isFindKey,
  markParts,
  matchRanges,
  parseQuery,
  rank,
  readFound,
  readOutline,
  searchChat,
  snippetAround,
  stepIndex,
  type Query,
} from "./find";
import type { ThreadEntry, Turn } from "./thread";

// The page helper's copy of the rules, taken out of bridge.js as it is served.
const begin = bridgeText.indexOf("// ---- find: matching");
const end = bridgeText.indexOf("// ---- find: end of matching ----");
type Page = {
  findFold: typeof fold;
  findQuery: typeof parseQuery;
  findRanges: typeof matchRanges;
  findRank: typeof rank;
  findSnippet: typeof snippetAround;
};
const page = new Function(
  `${bridgeText.slice(begin, end)}\nreturn { findFold, findQuery, findRanges, findRank, findSnippet };`,
)() as Page;

const QUERIES = [
  "revenue",
  "Total Revenue",
  "  total   revenue ",
  '"total revenue"',
  "“total revenue”",
  '"grew by',
  "cafe",
  "CAFÉ",
  "naïve résumé",
  "x",
  "",
  '""',
  "ﬁnal",
  "second section",
];
const TEXTS = [
  "Our total revenue grew by 12%",
  "Revenue, total: 4 million",
  "  The   Café   on the corner ",
  "A naive resume, and a NAÏVE RÉSUMÉ",
  "The final word",
  "Second Section",
  "",
  "İstanbul revenue",
  // Marks outside the Latin block: every nonspacing mark (\p{Mn}) is dropped, as the server does.
  "\u0645\u064e\u0631\u0652\u062d\u064e\u0628\u064b\u0627 and \u05e9\u05b8\u05c1\u05dc\u05d5\u05b9\u05dd",
];

describe("find matching", () => {
  it("drops every nonspacing mark, not only the Latin accents", () => {
    expect(fold("\u0645\u064e\u0631\u0652\u062d\u064e\u0628\u064b\u0627").text).toBe("\u0645\u0631\u062d\u0628\u0627");
    expect(fold("\u05e9\u05b8\u05c1\u05dc\u05d5\u05b9\u05dd").text).toBe("\u05e9\u05dc\u05d5\u05dd");
  });
  it("folds accents, case and white space, keeping where each character came from", () => {
    const folded = fold("  Café  Ünïcode ");
    expect(folded.text).toBe("cafe unicode");
    expect(folded.map[3]).toBe(5); // the é
    expect(folded.map[5]).toBe(8); // the Ü
  });

  it("reads words, quoted phrases (straight or curly, unclosed to the end) and refuses near-empty queries", () => {
    expect(parseQuery("Total  Revenue")).toEqual({ terms: ["total", "revenue"], whole: "total revenue" });
    expect(parseQuery('"total revenue" 2026')?.terms).toEqual(["total revenue", "2026"]);
    expect(parseQuery("“total revenue”")?.terms).toEqual(["total revenue"]);
    expect(parseQuery('grew "by 12')?.terms).toEqual(["grew", "by 12"]);
    expect(parseQuery("x")).toBeNull();
    expect(parseQuery("   ")).toBeNull();
    expect(parseQuery('""')).toBeNull();
  });

  it("matches a piece only when it holds every word, and gives the ranges in the original text", () => {
    const q = parseQuery("revenue total") as Query;
    expect(matchRanges("Our total revenue grew", q)).toEqual([
      [4, 9],
      [10, 17],
    ]);
    expect(matchRanges("Our total revenue grew", parseQuery('"total revenue"'))).toEqual([[4, 17]]);
    expect(matchRanges("Our revenue grew", q)).toBeNull();
    expect(matchRanges("A Café here", parseQuery("cafe"))).toEqual([[2, 6]]);
    // A phrase must be one run; words need not be.
    expect(matchRanges("revenue and total", parseQuery('"total revenue"'))).toBeNull();
    expect(matchRanges("revenue and total", parseQuery("total revenue"))).toEqual([
      [0, 7],
      [12, 17],
    ]);
  });

  it("ranks headings first, then the whole query as one run, then page order", () => {
    const q = parseQuery("total revenue") as Query;
    const items = [
      { text: "revenue, total", heading: false },
      { text: "the total revenue", heading: false },
      { text: "Revenue in total", heading: true },
      { text: "total revenue again", heading: false },
    ];
    expect(rank(items, q)).toEqual([2, 1, 3, 0]);
  });

  it("cuts a snippet round the first match", () => {
    const long = `${"word ".repeat(40)}the total revenue is here ${"tail ".repeat(60)}`;
    const snippet = snippetAround(long, parseQuery("total revenue"));
    expect(snippet.startsWith("…")).toBe(true);
    expect(snippet.endsWith("…")).toBe(true);
    expect(snippet).toContain("the total revenue is here");
    expect(snippetAround("Short text", parseQuery("text"))).toBe("Short text");
  });

  it("bolds every term found in a snippet, even one cut short of the others", () => {
    expect(markParts("A Café and more", parseQuery("cafe more"))).toEqual([
      { text: "A ", hit: false },
      { text: "Café", hit: true },
      { text: " and ", hit: false },
      { text: "more", hit: true },
    ]);
    expect(markParts("…only cafe", parseQuery("cafe gone"))).toEqual([
      { text: "…only ", hit: false },
      { text: "cafe", hit: true },
    ]);
  });

  it("follows the same rules on the page as in the studio (bridge.js)", () => {
    expect(begin).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(begin);
    for (const text of TEXTS) expect(page.findFold(text)).toEqual(fold(text));
    for (const raw of QUERIES) {
      const ours = parseQuery(raw);
      expect(page.findQuery(raw)).toEqual(ours);
      for (const text of TEXTS) {
        expect(page.findRanges(text, ours)).toEqual(matchRanges(text, ours));
        expect(page.findSnippet(text, ours)).toEqual(snippetAround(text, ours));
      }
      if (ours) {
        const items = TEXTS.map((text, index) => ({ text, heading: index === 5 }));
        expect(page.findRank(items, ours)).toEqual(rank(items, ours));
      }
    }
  });
});

function turn(key: string, text: string, messages: string[], tool?: string): Turn {
  return {
    type: "turn",
    key,
    id: key,
    seq: 1,
    startedAt: "",
    text,
    selection: null,
    connection: null,
    model: null,
    attachments: [],
    queuedId: null,
    status: "completed",
    error: null,
    usage: null,
    finishedAt: null,
    plan: null,
    changedPaths: [],
    items: [
      ...messages.map((message, index) => ({ type: "message" as const, key: `${key}-m${index}`, messageId: `m${index}`, text: message })),
      { type: "thought" as const, key: `${key}-t`, messageId: "t", text: "thinking about revenue" },
      ...(tool
        ? [
            {
              type: "tool" as const,
              key: `${key}-tool`,
              tool: { toolId: "x", kind: "read", title: "Read", status: "ok" as const, children: [], output: tool } as never,
            },
          ]
        : []),
    ],
  } as Turn;
}

describe("find in the chat", () => {
  const entries: ThreadEntry[] = [
    turn("t1", "Make a page about revenue", ["I added a revenue table."], "revenue in a tool output"),
    turn("t2", "Now the costs", ["Costs are in, next to the total revenue."]),
  ];

  it("searches the person's messages and the agent's prose, not thinking or tool output", () => {
    const { hits, capped } = searchChat(entries, "revenue");
    expect(capped).toBe(false);
    expect(hits.map((hit) => hit.key)).toEqual([chatKey.you("t1"), chatKey.agent("t1-m0"), chatKey.agent("t2-m0")]);
    expect(hits[0]).toMatchObject({ kind: "you", section: null, snippet: "Make a page about revenue" });
    expect(hits[2]).toMatchObject({ kind: "agent", section: "Now the costs" });
  });

  it("puts the whole phrase first and caps the list", () => {
    expect(searchChat(entries, "revenue total").hits.map((hit) => hit.key)).toEqual([chatKey.agent("t2-m0")]);
    const many = searchChat(entries, "revenue", 2);
    expect(many.hits).toHaveLength(2);
    expect(many.capped).toBe(true);
    expect(searchChat(entries, "").hits).toEqual([]);
  });
});

describe("find messages and keys", () => {
  it("keeps only well-formed hits from the page", () => {
    const found = readFound({
      id: 3,
      total: 5,
      capped: false,
      hits: [
        { n: 0, kind: "heading", section: null, snippet: "Revenue", hidden: false },
        { n: 1, kind: "odd", section: "Costs", snippet: "the revenue", hidden: true },
        { n: -1, snippet: "bad" },
        { n: 2, snippet: "" },
        { n: 1, kind: "text", section: null, snippet: "the same number again", hidden: false },
        "junk",
      ],
    });
    expect(found).toEqual({
      id: 3,
      total: 5,
      capped: false,
      partial: false,
      hits: [
        { n: 0, kind: "heading", section: null, snippet: "Revenue", hidden: false },
        { n: 1, kind: "text", section: "Costs", snippet: "the revenue", hidden: true },
      ],
    });
    expect(readFound({ id: 1, hits: [], partial: true })?.partial).toBe(true);
    expect(readFound({ id: 1, hits: [], partial: "yes" })?.partial).toBe(false);
    expect(readFound({ hits: [] })).toBeNull();
    expect(readOutline({ items: [{ n: 0, level: 9, text: "Top" }, { n: 1, text: "" }] })).toEqual([
      { n: 0, level: 6, text: "Top", hidden: false },
    ]);
  });

  it("walks the rows round in both directions", () => {
    expect(stepIndex(-1, 3, 1)).toBe(0);
    expect(stepIndex(-1, 3, -1)).toBe(2);
    expect(stepIndex(2, 3, 1)).toBe(0);
    expect(stepIndex(0, 3, -1)).toBe(2);
    expect(stepIndex(0, 0, 1)).toBe(-1);
    const go = () => {};
    expect(
      flatRows([
        { id: "a", title: "A", rows: [{ key: "1", section: null, snippet: "x", kind: "text", onGo: go }] },
        { id: "b", title: "B", rows: [{ key: "2", section: null, snippet: "y", kind: "you", onGo: go }] },
      ]).map((row) => row.key),
    ).toEqual(["1", "2"]);
  });

  it("is Cmd+F on a Mac and Ctrl+F elsewhere", () => {
    const key = (over: Partial<KeyboardEvent>) => ({ key: "f", metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, ...over });
    expect(isFindKey(key({ metaKey: true }), true)).toBe(true);
    expect(isFindKey(key({ ctrlKey: true }), true)).toBe(false); // moves the cursor in a Mac text box
    expect(isFindKey(key({ ctrlKey: true }), false)).toBe(true);
    expect(isFindKey(key({ ctrlKey: true, key: "F", shiftKey: true }), false)).toBe(false);
    expect(isFindKey(key({ metaKey: true, key: "g" }), true)).toBe(false);
  });
});
