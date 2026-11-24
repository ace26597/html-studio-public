import { describe, expect, it } from "vitest";
import {
  blockMatches,
  checkPageLine,
  countElsewhere,
  countRows,
  countWords,
  elsewhereCounts,
  firstKindFound,
  MAIN_SECTIONS,
  moreMatchCount,
  splitSections,
  splitServers,
  enabledMcpCount,
  filterSections,
  groupMcp,
  groupSkills,
  librarySections,
  matchesQuery,
  MCP_LIMIT,
  mcpLimitReached,
  mcpTarget,
  nameProblem,
  setEnabled,
  skillName,
} from "./library";
import type { Library, LibraryIndex, McpServer, ProjectLibrary } from "./types";

// Shapes for the rules under test only; not product data.
const server = (id: string, source: string, extra: Partial<McpServer> = {}): McpServer => ({
  id,
  name: id,
  label: null,
  transport: "http",
  url: `https://example.test/${id}`,
  command: null,
  args: [],
  secret_names: [],
  source,
  description: null,
  last_test: null,
  ...extra,
});

const base: Library = {
  connections: [
    { id: "a", label: "Conn A", kind: "claude-subscription", available: true, reason: null, models: [{ id: "m1", label: "Model 1" }], default_model: "m1" },
    { id: "b", label: "Conn B", kind: "anthropic-gateway", available: false, reason: "Key not set", models: [], default_model: null },
  ],
  tools: [
    { id: "t1", label: "Tool one", description: "Does one", available: true, note: null },
    { id: "t2", label: "Tool two", description: "Does two", available: false, note: "Missing on this machine" },
  ],
  studio_tools: [
    { name: "fetch_source", description: "Saves a source", available: true, note: null },
    { name: "t1", description: "duplicate of a tool id", available: true, note: null },
  ],
  agents: [{ name: "researcher", description: "Finds sources" }],
  skills: [
    { id: "studio:s1", name: "s1", label: "Studio skill", description: "From the studio", source: "studio", default: true },
    { id: "yours:mine", name: "mine", label: "Mine", description: "Mine", source: "yours", default: true },
    { id: "machine:m", label: "Machine skill", description: "On the Mac", source: "machine", default: false },
    { id: "built-in:b", label: "Built skill", description: "Built in", source: "built-in" },
  ],
  mcp_servers: [server("y1", "yours"), server("m1", "machine"), server("s1", "suggested")],
  templates: [{ id: "x", label: "Tpl", description: "d", app: true }],
  data_kinds: [
    { kind: "postgres", label: "PostgreSQL", default_port: 5432, available: true, note: null },
    { kind: "mysql", label: "MySQL", default_port: 3306, available: false, note: "pip install x" },
  ],
  observed_at: "2026-11-10T10:00:00Z",
};

const section = (lib: Library, id: string) => librarySections(lib).find((item) => item.id === id)!;

describe("librarySections", () => {
  it("lists connections with availability and the reason when unavailable", () => {
    const rows = section(base, "connections").rows;
    expect(rows[0].status).toEqual({ tone: "ok", text: "Available" });
    expect(rows[1]).toMatchObject({ status: { tone: "off" }, note: "Key not set" });
  });

  it("groups skills by source with plain titles, in order", () => {
    const ids = librarySections(base).map((s) => s.id).filter((id) => id.startsWith("skills-"));
    expect(ids).toEqual(["skills-studio", "skills-yours", "skills-machine", "skills-built-in"]);
    expect(section(base, "skills-yours").title).toBe("Your own");
    expect(section(base, "skills-machine").title).toBe("On this computer");
    expect(section(base, "skills-machine").intro).toContain("Picked for a request when they fit");
    expect(section(base, "skills-built-in").title).toBe("Built into the assistant");
  });

  it("shows built-in skills by name only and lets only yours be deleted", () => {
    expect(section(base, "skills-built-in").rows[0].detail).toBeNull();
    expect(section(base, "skills-yours").rows[0].action).toEqual({ kind: "delete-skill", name: "mine" });
    expect(section(base, "skills-studio").rows[0].action).toBeUndefined();
    expect(section(base, "skills-machine").rows[0].action).toBeUndefined();
  });

  it("does not guess built-in skills before the first agent run", () => {
    const fresh: Library = { ...base, observed_at: null, skills: base.skills.filter((s) => s.source !== "built-in") };
    expect(section(fresh, "skills-built-in").rows).toEqual([]);
    expect(section(fresh, "skills-built-in").empty).toBe("This list appears after the first agent run.");
  });

  it("lists studio tools without repeating one already in the tools list", () => {
    expect(section(base, "studio-tools").rows.map((row) => row.title)).toEqual(["fetch_source"]);
  });

  it("lists sub-agents", () => {
    expect(section(base, "agents").rows[0]).toMatchObject({ title: "researcher", detail: "Finds sources" });
  });

  it("leaves out Step 3 sections a Step 2b server does not send", () => {
    const old: Library = { ...base, studio_tools: undefined, agents: undefined };
    const ids = librarySections(old).map((s) => s.id);
    expect(ids).not.toContain("studio-tools");
    expect(ids).not.toContain("agents");
  });

  it("names database types whose driver is missing", () => {
    expect(section(base, "data-kinds").rows[1]).toMatchObject({ status: { tone: "off", text: "Driver not installed" } });
  });

  it("counts and filters across sections, every word must match", () => {
    const sections = librarySections(base);
    expect(countRows(sections)).toBe(13);
    expect(countRows(filterSections(sections, "driver"))).toBe(1);
    expect(countRows(filterSections(sections, "tool missing"))).toBe(1);
    expect(countRows(filterSections(sections, "nothing-like-this"))).toBe(0);
  });
});

describe("skills and servers", () => {
  it("groups skills and servers, keeping unknown sources visible", () => {
    const skills = groupSkills([...base.skills, { id: "x:y", label: "Odd", description: "", source: "odd" }]);
    expect(skills.map((g) => g.source)).toEqual(["studio", "yours", "machine", "built-in", "other"]);
    expect(groupMcp(base.mcp_servers).map((g) => [g.title, g.items.length])).toEqual([
      ["Your own", 1],
      ["On this computer", 1],
      ["Suggested", 1],
    ]);
  });

  it("finds a skill's name", () => {
    expect(skillName({ id: "studio:deep-research", label: "", description: "", source: "studio" })).toBe("deep-research");
    expect(skillName({ id: "x", name: "given", label: "", description: "", source: "yours" })).toBe("given");
  });

  it("checks names", () => {
    expect(nameProblem("docs", 40, "server")).toBeNull();
    expect(nameProblem("", 40, "server")).toBe("Give it a name.");
    expect(nameProblem("Docs", 40, "server")).toContain("lower-case");
    expect(nameProblem("studio", 40, "server")).toContain("reserved");
    expect(nameProblem("studio", 60, "skill")).toBeNull();
    expect(nameProblem("a".repeat(41), 40, "server")).toContain("40");
  });

  it("matches a query word by word", () => {
    expect(matchesQuery(["Cloudflare docs", "https://x"], "docs cloud")).toBe(true);
    expect(matchesQuery(["Cloudflare docs"], "github")).toBe(false);
    expect(matchesQuery([null, undefined], "")).toBe(true);
  });

  it("says where a server lives", () => {
    expect(mcpTarget(server("a", "yours"))).toBe("https://example.test/a");
    expect(mcpTarget({ transport: "stdio", url: null, command: "npx", args: ["-y", "pkg"] })).toBe("npx -y pkg");
  });
});

describe("project switches", () => {
  const lib = (on: number, total = 14): ProjectLibrary => ({
    skills: [{ ...base.skills[0], enabled: true }],
    mcp_servers: Array.from({ length: total }, (_, i) => ({ ...server(`s${i}`, "suggested"), enabled: i < on })),
  });

  it("flips one switch without touching the rest", () => {
    const before = lib(1, 3);
    const after = setEnabled(before, "mcp_servers", "s2", true);
    expect(after.mcp_servers.map((s) => s.enabled)).toEqual([true, false, true]);
    expect(before.mcp_servers[2].enabled).toBe(false);
    expect(setEnabled(before, "skills", "studio:s1", false).skills[0].enabled).toBe(false);
  });

  it("enforces the 12-server limit only when switching on", () => {
    expect(MCP_LIMIT).toBe(12);
    const full = lib(12);
    expect(enabledMcpCount(full)).toBe(12);
    expect(mcpLimitReached(full, "s13")).toBe(true);
    expect(mcpLimitReached(full, "s0")).toBe(false);
    expect(mcpLimitReached(lib(11), "s13")).toBe(false);
  });
});

describe("checkPageLine", () => {
  it("reads the page check summary as one line", () => {
    const json = JSON.stringify({ page: "index.html", summary: { verified: 31, derived: 4, failed: 2, unmarked_numbers: 7, sources_cited: 9, sources_unused: 1 } });
    expect(checkPageLine(json)).toBe("31 verified · 4 derived · 2 failed · 7 unmarked");
  });

  it("leaves anything else alone", () => {
    expect(checkPageLine(null)).toBeNull();
    expect(checkPageLine("not json")).toBeNull();
    expect(checkPageLine(JSON.stringify({ summary: { verified: "31" } }))).toBeNull();
    expect(checkPageLine(JSON.stringify([1, 2]))).toBeNull();
  });
});

describe("what the Library opens on, and Everything else", () => {
  it("opens on the studio's and your own skills, then templates; the rest is under Everything else", () => {
    const { main, more } = splitSections(librarySections(base));
    expect(main.map((s) => s.id)).toEqual(["skills-studio", "skills-yours", "templates"]);
    expect(more.map((s) => s.id)).toEqual([
      "connections",
      "tools",
      "studio-tools",
      "skills-machine",
      "skills-built-in",
      "agents",
      "data-kinds",
    ]);
    // Nothing is lost in the split.
    expect(main.length + more.length).toBe(librarySections(base).length);
    expect(MAIN_SECTIONS).toEqual(["skills-studio", "skills-yours", "templates"]);
  });

  it("keeps your own and the suggested connectors up front; the ones found on this computer go under Everything else", () => {
    const servers = [...base.mcp_servers, server("p1", "plugin"), server("a1", "app"), server("o1", "odd")];
    const { main, more } = splitServers(servers);
    expect(main.map((s) => s.id)).toEqual(["y1", "s1", "a1"]);
    expect(more.map((s) => s.id)).toEqual(["m1", "p1", "o1"]);
  });

  it("counts what the index found outside what is shown up front", () => {
    expect(countElsewhere("skills", { studio: 24, yours: 2, machine: 43, plugin: 441 })).toBe(484);
    expect(countElsewhere("connectors", { yours: 1, suggested: 9, machine: 4, plugin: 17 })).toBe(21);
    expect(countElsewhere("agents", { studio: 4, plugin: 57 })).toBe(61);
    expect(countElsewhere("skills", undefined)).toBe(0);
    const index = {
      skills: 0,
      agents: 0,
      connectors: 0,
      by_source: { skills: { studio: 1, machine: 3 }, agents: { studio: 2 }, connectors: { suggested: 5, plugin: 1 } },
    } as unknown as LibraryIndex;
    expect(elsewhereCounts(index)).toEqual({ skills: 3, connectors: 1, agents: 2 });
    expect(elsewhereCounts(null)).toEqual({ skills: 0, connectors: 0, agents: 0 });
  });

  it("finds blocks by title or words, only while searching", () => {
    const keys = { title: "Keys for other services", words: "api token search" };
    expect(blockMatches(keys, "token")).toBe(true);
    expect(blockMatches(keys, "keys services")).toBe(true);
    expect(blockMatches(keys, "postgres")).toBe(false);
    expect(blockMatches(keys, "  ")).toBe(false);
  });

  it("counts what a search finds in the closed part, so it can be shown", () => {
    const { more } = splitSections(librarySections(base));
    const blocks = [{ title: "Data sources", words: "database postgres" }];
    // "researcher" is a helper agent: only under Everything else.
    expect(moreMatchCount({ sections: more, servers: 0, blocks, query: "researcher" })).toBe(1);
    // "postgres" is a database type row and the Data sources block.
    expect(moreMatchCount({ sections: more, servers: 0, blocks, query: "postgres" })).toBe(2);
    // What the index of this computer found, and matching connectors, count too.
    expect(moreMatchCount({ sections: more, servers: 2, blocks, query: "pdf", index: { skills: 5, connectors: 0, agents: 1 } })).toBe(8);
    expect(moreMatchCount({ sections: more, servers: 0, blocks, query: "nothing-like-this" })).toBe(0);
    // No search, nothing to show.
    expect(moreMatchCount({ sections: more, servers: 3, blocks, query: "" })).toBe(0);
  });

  it("opens the index list on the first kind a search found", () => {
    expect(firstKindFound({ skills: 0, connectors: 2, agents: 1 })).toBe("connectors");
    expect(firstKindFound({ skills: 0, connectors: 0, agents: 0 })).toBeNull();
    expect(firstKindFound(null)).toBeNull();
  });

  it("says counts in words", () => {
    expect(countWords(1, "skills")).toBe("1 skill");
    expect(countWords(484, "skills")).toBe("484 skills");
    expect(countWords(2, "agents")).toBe("2 helper agents");
    expect(countWords(1200, "connectors")).toBe("1,200 connectors");
  });
});
