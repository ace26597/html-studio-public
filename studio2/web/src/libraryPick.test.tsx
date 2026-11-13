import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { libraryItemsSearch } from "./api";
import { DecisionControl } from "./LibraryControls";
import { LibraryPickCard, PickList } from "./LibraryPickCard";
import {
  connectorState,
  decisionBody,
  indexSummary,
  listSize,
  mergePage,
  PAGE_SIZE,
  pickHeadline,
  remaining,
  saveDecision,
  savedWords,
  skillFacts,
  withDecision,
} from "./libraryPick";
import { enabledAfter, pinnedConnectors, withProjectChoice } from "./ProjectCapabilities";
import { initialThread, reduceEvents } from "./thread";
import type { LibraryItem, LibraryItemsPage, LibraryPick, ProjectLibrary, StudioEvent } from "./types";

const index = {
  skills: 510,
  agents: 60,
  connectors: 33,
  by_source: { skills: {}, agents: {}, connectors: {} },
  plugins: 94,
  plugins_enabled: 80,
  duplicates: 3,
  built_at: "2026-11-12T10:00:00Z",
  build_ms: 712,
};

const skill = (n: number, extra: Partial<LibraryItem> = {}): LibraryItem => ({
  id: `plugin:p@m:s${n}`,
  name: `s${n}`,
  label: `Skill ${n}`,
  description: "Does a thing",
  source: "plugin",
  origin: "Plugin p@m",
  ...extra,
});

/** A server answering pages of a 2,000-skill index, like `GET /api/library/items`. */
function pageOf(offset: number, limit = PAGE_SIZE, total = 2000): LibraryItemsPage {
  const items = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, i) => skill(offset + i));
  return { items, total, offset, limit, counts: { plugin: total }, index };
}

const pick: LibraryPick = {
  mode: "model",
  skills: [
    { id: "plugin:p@m:pdf", name: "pdf", label: "PDF", source: "plugin", origin: "Plugin p@m", how: "picked", reason: "Reads PDFs" },
    { id: "studio:deep-research", name: "deep-research", label: "Deep research", source: "studio", origin: null, how: "pinned", reason: null },
  ],
  connectors: [
    {
      id: "machine:linear",
      name: "linear",
      label: "Linear",
      source: "machine",
      origin: "~/.claude.json",
      how: "picked",
      reason: "Tickets",
      state: "needs-auth",
      fix: "claude mcp login Linear",
    },
  ],
  list_chars: 7412,
  budget_chars: 8000,
  loaded_skills: 31,
  ms: 2140,
  total_ms: 2390,
  note: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the paged list", () => {
  it("asks for one page at a time and appends without duplicates", () => {
    let rows = mergePage(null, pageOf(0), false);
    expect(rows).toHaveLength(50);
    rows = mergePage(rows, pageOf(rows.length), true);
    rows = mergePage(rows, pageOf(rows.length), true);
    expect(rows).toHaveLength(150);
    expect(new Set(rows.map((row) => row.id)).size).toBe(150);
    expect(remaining(rows.length, 2000)).toBe(1850);
    // A repeated page (a double click) adds nothing.
    expect(mergePage(rows, pageOf(100), true)).toHaveLength(150);
    // A new search replaces the list.
    expect(mergePage(rows, pageOf(0, 50, 7), false)).toHaveLength(7);
    expect(remaining(7, 7)).toBe(0);
  });

  it("builds the query string, leaving out empty values", () => {
    expect(libraryItemsSearch({ kind: "skills" })).toBe("kind=skills");
    expect(
      libraryItemsSearch({ kind: "connectors", q: " contract ", source: "plugin", state: "signin", offset: 50, limit: 50 }),
    ).toBe("kind=connectors&q=contract&source=plugin&state=signin&offset=50&limit=50");
    expect(libraryItemsSearch({ kind: "skills", q: "x", project: "p1", limit: 20 })).toBe("kind=skills&q=x&limit=20&project=p1");
  });

  it("says what was found", () => {
    expect(indexSummary(index)).toBe(
      "510 skills, 60 helper agents and 33 connectors found in 94 plugins and your own folders; index built in 0.7 s",
    );
  });

  it("states a skill's facts", () => {
    expect(skillFacts(skill(1, { picked: 3, used: 1 }))).toEqual(["Picked 3 times", "used 1 time"]);
    expect(skillFacts(skill(1, { picked: 5, used: 0, benched: true }))).toEqual([
      "No longer picked: chosen 5 times and never used",
    ]);
    expect(skillFacts(skill(1, { enabled: false }))).toEqual(["plugin switched off in Claude Code"]);
  });

  it("describes a connector's state", () => {
    expect(connectorState("needs-auth", "claude mcp login x")).toMatchObject({ text: "Needs signing in", command: "claude mcp login x" });
    expect(connectorState("needs-setting", "Set KEY")).toMatchObject({ sentence: "Set KEY" });
    expect(connectorState("failed", null)?.text).toBe("Could not connect last time");
    expect(connectorState("ok", null)).toBeNull();
  });
});

describe("Auto / Always / Never", () => {
  function stubFetch() {
    const calls: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, method: String(init.method), body: init.body ? JSON.parse(String(init.body)) : undefined });
        return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
      }),
    );
    return calls;
  }

  it("saves the whole computer's word through PUT /api/library/prefs", async () => {
    const calls = stubFetch();
    await saveDecision("skills", "plugin:p@m:pdf", "machine", "always");
    await saveDecision("mcp_servers", "machine:linear", "machine", "never");
    await saveDecision("skills", "plugin:p@m:pdf", "machine", "auto");
    expect(calls).toEqual([
      { url: "/api/library/prefs", method: "PUT", body: { skills: { "plugin:p@m:pdf": "always" } } },
      { url: "/api/library/prefs", method: "PUT", body: { mcp_servers: { "machine:linear": "never" } } },
      { url: "/api/library/prefs", method: "PUT", body: { skills: { "plugin:p@m:pdf": null } } },
    ]);
  });

  it("saves this project's word through PUT /api/projects/{id}/library as true / false / null", async () => {
    const calls = stubFetch();
    await saveDecision("skills", "plugin:p@m:pdf", "project", "always", "p1");
    await saveDecision("mcp_servers", "machine:linear", "project", "never", "p1");
    await saveDecision("skills", "plugin:p@m:pdf", "project", "auto", "p1");
    expect(calls.map((call) => [call.url, call.method, call.body])).toEqual([
      ["/api/projects/p1/library", "PUT", { skills: { "plugin:p@m:pdf": true } }],
      ["/api/projects/p1/library", "PUT", { mcp_servers: { "machine:linear": false } }],
      ["/api/projects/p1/library", "PUT", { skills: { "plugin:p@m:pdf": null } }],
    ]);
  });

  it("refuses a project decision without a project, without calling the server", async () => {
    const calls = stubFetch();
    await expect(saveDecision("skills", "x", "project", "always", null)).rejects.toThrow();
    expect(calls).toHaveLength(0);
  });

  it("builds bodies and optimistic rows", () => {
    expect(decisionBody("skills", "a", "project", "never")).toEqual({ skills: { a: false } });
    expect(decisionBody("mcp_servers", "a", "machine", "auto")).toEqual({ mcp_servers: { a: null } });
    expect(withDecision(skill(1), "machine", "always").pref).toBe("always");
    expect(withDecision(skill(1, { choice: true }), "project", "auto").choice).toBeNull();
    expect(savedWords("project", "always")).toBe("Always in this project");
    expect(savedWords("machine", "never")).toBe("Never on this computer");
  });

  it("renders as one radio group with the current value checked", () => {
    const html = renderToStaticMarkup(<DecisionControl value="never" label="PDF on this computer" onChange={() => {}} />);
    expect(html).toContain('role="radiogroup"');
    expect(html).toContain('aria-label="PDF on this computer"');
    expect(html.match(/role="radio"/g)).toHaveLength(3);
    expect(html).toMatch(/aria-checked="true"[^>]*>Never</);
  });
});

describe("the project panel", () => {
  const library: ProjectLibrary = {
    skills: [
      { id: "studio:a", name: "a", label: "A", description: "", source: "studio", enabled: true, choice: null, pref: null },
      { id: "plugin:p@m:b", name: "b", label: "B", description: "", source: "plugin", enabled: false, choice: null, pref: "never" },
    ],
    mcp_servers: [
      {
        id: "machine:x",
        name: "x",
        label: null,
        transport: "http",
        url: "https://x",
        command: null,
        args: [],
        secret_names: [],
        source: "machine",
        description: null,
        last_test: null,
        enabled: false,
        choice: null,
        pref: null,
      },
    ],
    requested: [],
    last_pick: null,
  };

  it("works out what a turn loads without being asked", () => {
    expect(enabledAfter(null, null, true)).toBe(true);
    expect(enabledAfter(false, null, true)).toBe(false);
    expect(enabledAfter(null, "always", false)).toBe(true);
    expect(enabledAfter(true, "never", false)).toBe(true);
  });

  it("changes one item's project word, and rolls it back", () => {
    const off = withProjectChoice(library, "skills", "studio:a", false);
    expect(off.skills[0]).toMatchObject({ choice: false, enabled: false });
    const back = withProjectChoice(off, "skills", "studio:a", null);
    expect(back.skills[0]).toMatchObject({ choice: null, enabled: true });
    const pinned = withProjectChoice(library, "mcp_servers", "machine:x", true);
    expect(pinned.mcp_servers[0]).toMatchObject({ choice: true, enabled: true });
    expect(pinnedConnectors(pinned)).toBe(1);
    // The project's word wins over the machine's "never".
    expect(withProjectChoice(library, "skills", "plugin:p@m:b", true).skills[1].enabled).toBe(true);
  });
});

describe("the picked card", () => {
  it("says what was added on top of the studio's own skills", () => {
    expect(pickHeadline(pick)).toBe("Picked 2 skills and 1 connector for this request");
    expect(pickHeadline({ ...pick, mode: "all", connectors: [], loaded_skills: 12 })).toBe(
      "Loaded your whole library (12 skills)",
    );
    expect(pickHeadline({ ...pick, skills: [], connectors: [] })).toBeNull();
    expect(listSize(pick)).toBe("7,412 of 8,000 characters");
  });

  it("renders nothing when nothing was added", () => {
    expect(renderToStaticMarkup(<LibraryPickCard pick={{ ...pick, skills: [], connectors: [] }} projectId="p1" />)).toBe("");
  });

  it("starts closed, as one line", () => {
    const html = renderToStaticMarkup(<LibraryPickCard pick={pick} projectId="p1" />);
    expect(html).toContain("Picked 2 skills and 1 connector for this request");
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("Reads PDFs");
  });

  it("lists each item with its reason, Always / Never and a scope (this project first)", () => {
    const html = renderToStaticMarkup(<PickList pick={{ ...pick, note: "The fast model took too long." }} projectId="p1" decide />);
    expect(html).toContain("Reads PDFs");
    expect(html).toContain("chosen by the fast model");
    expect(html).toContain("7,412 of 8,000 characters");
    expect(html).toContain("The fast model took too long.");
    expect(html).toContain("Needs signing in");
    expect(html).toContain("claude mcp login Linear");
    expect(html).toContain('aria-label="Always use PDF"');
    expect(html).toContain('aria-label="Never use Linear"');
    // The studio's own skill gets no buttons.
    expect(html).not.toContain('aria-label="Always use Deep research"');
    expect(html).toMatch(/<option value="project" selected="">this project<\/option>/);
  });

  it("is kept on its turn by the reducer, and unknown events stay harmless", () => {
    const events = [
      { seq: 1, ts: "t", turn_id: "t1", type: "turn_started", text: "hi", selection: null, connection: null, model: null },
      { seq: 2, ts: "t", turn_id: "t1", type: "library_picked", ...pick },
      { seq: 3, ts: "t", turn_id: "t1", type: "something_new", whatever: 1 },
      { seq: 4, ts: "t", turn_id: "t1", type: "library_picked", mode: "words" },
    ] as unknown as StudioEvent[];
    const before = reduceEvents(initialThread, events.slice(0, 3));
    const turn = before.entries[0];
    expect(turn.type === "turn" && turn.pick?.skills).toHaveLength(2);
    expect(before.lastSeq).toBe(3);
    // A malformed event still gives lists, never undefined.
    const after = reduceEvents(before, events.slice(3));
    const last = after.entries[0];
    expect(last.type === "turn" && last.pick).toMatchObject({ mode: "words", skills: [], connectors: [], note: null });
  });
});
