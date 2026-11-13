import { describe, expect, it } from "vitest";
import {
  checkPageLine,
  countRows,
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
import type { Library, McpServer, ProjectLibrary } from "./types";

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
    expect(section(base, "skills-yours").title).toBe("Your skills");
    expect(section(base, "skills-machine").title).toBe("On this machine");
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
      ["Your servers", 1],
      ["On this machine", 1],
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
