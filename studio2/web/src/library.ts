// The Library ("What this studio can do"): pure views over `GET /api/library`.

import type { Library, LibraryIndex, LibrarySkill, McpServer, ProjectLibrary } from "./types";

/** Show a filter box once the library lists more than this many items. */
export const FILTER_THRESHOLD = 12;

export type RowTone = "ok" | "off" | "neutral";

export interface LibraryRow {
  key: string;
  title: string;
  detail: string | null;
  status: { tone: RowTone; text: string } | null;
  note: string | null;
  /** A row the person can act on (a skill they added can be deleted). */
  action?: { kind: "delete-skill"; name: string };
  /** A skill that can be downloaded as a file: its id in the index. */
  exportId?: string;
}

export interface LibrarySection {
  id:
    | "connections"
    | "tools"
    | "studio-tools"
    | "skills-studio"
    | "skills-yours"
    | "skills-machine"
    | "skills-built-in"
    | "skills-other"
    | "agents"
    | "templates"
    | "data-kinds";
  title: string;
  /** One line under the title saying where the list comes from, when that matters. */
  intro: string | null;
  rows: LibraryRow[];
  /** What to say when the section has no rows (before filtering). */
  empty: string;
}

const available = (ok: boolean, offText = "Not available") =>
  ok ? { tone: "ok" as const, text: "Available" } : { tone: "off" as const, text: offText };

/**
 * The sections, in the order they are shown. `observedLabel` is how to name
 * `observed_at` in prose (for example a formatted date); it is only used when
 * `observed_at` is set. Built-in skills and MCP servers are never guessed: with
 * no observation they are empty and say when they will appear.
 */
export interface SkillGroup<T extends LibrarySkill = LibrarySkill> {
  source: string;
  title: string;
  intro: string | null;
  items: T[];
}

/** Where a skill must come from to be in the index, and so to have a file to export. */
const EXPORTABLE = new Set(["studio", "yours", "machine", "plugin"]);

const SKILL_GROUPS: { source: string; title: string; intro: string | null }[] = [
  { source: "studio", title: "From the studio", intro: "Come with the studio. On in every project unless you switch them off there." },
  { source: "yours", title: "Your own", intro: "Added by you in this studio. On in every project unless you switch them off there." },
  { source: "machine", title: "On this computer", intro: "Found in ~/.claude/skills on this computer. Picked for a request when they fit." },
  { source: "built-in", title: "Built into the assistant", intro: "Reported by the assistant itself; they can’t be switched." },
];

/** Skills in the four groups, in this order. Unknown sources join "On this computer"'s neighbour list at the end. */
export function groupSkills<T extends LibrarySkill>(skills: readonly T[]): SkillGroup<T>[] {
  const known = new Set(SKILL_GROUPS.map((group) => group.source));
  const groups = SKILL_GROUPS.map((group) => ({ ...group, items: skills.filter((skill) => skill.source === group.source) }));
  const other = skills.filter((skill) => !known.has(skill.source));
  if (other.length) groups.push({ source: "other", title: "Other skills", intro: null, items: other });
  return groups;
}

/** The skill's folder name: `name`, or the part of the id after `source:`. */
export function skillName(skill: LibrarySkill): string {
  if (skill.name) return skill.name;
  const colon = skill.id.indexOf(":");
  return colon === -1 ? skill.id : skill.id.slice(colon + 1);
}

export interface McpGroup<T extends McpServer = McpServer> {
  source: string;
  title: string;
  intro: string | null;
  empty: string;
  items: T[];
}

const MCP_GROUPS: { source: string; title: string; intro: string | null; empty: string }[] = [
  { source: "yours", title: "Your own", intro: "Added in this studio. Secret values are kept in your Mac’s Keychain.", empty: "You haven’t added any connectors." },
  {
    source: "machine",
    title: "On this computer",
    intro: "Read from this computer’s Claude settings (~/.claude.json) when used. Picked for a request when they fit.",
    empty: "No connectors were found in this computer’s Claude settings.",
  },
  {
    source: "plugin",
    title: "From plugins",
    intro: "Come with your Claude Code plugins. Picked for a request when they fit.",
    empty: "No plugin brings a connector.",
  },
  {
    source: "app",
    title: "From this app",
    intro: "Declared in the attached app’s own .mcp.json. Used only when you say always.",
    empty: "This app declares no connectors.",
  },
  { source: "suggested", title: "Suggested", intro: "Public connectors that need no account. Switch them on in a project, or test them here.", empty: "No suggestions." },
];

export function groupMcp<T extends McpServer>(servers: readonly T[]): McpGroup<T>[] {
  const known = new Set(MCP_GROUPS.map((group) => group.source));
  const groups = MCP_GROUPS.map((group) => ({ ...group, items: servers.filter((server) => server.source === group.source) }))
    // Plugins and an attached app only get a group when they bring something.
    .filter((group) => (group.source !== "plugin" && group.source !== "app") || group.items.length > 0);
  const other = servers.filter((server) => !known.has(server.source));
  if (other.length) groups.push({ source: "other", title: "Other connectors", intro: null, empty: "", items: other });
  return groups;
}

/** At most this many MCP servers can be on in one project. */
export const MCP_LIMIT = 12;

/** Names for skills and MCP servers: lower-case letters, digits, `-` and `_`; MCP servers may not be called `studio`. */
export function nameProblem(name: string, max: number, kind: "skill" | "server"): string | null {
  if (!name) return "Give it a name.";
  if (name.length > max) return `The name can be at most ${max} characters.`;
  if (!/^[a-z0-9_-]+$/.test(name)) return "Use lower-case letters, digits, “-” and “_” only.";
  if (kind === "server" && name === "studio") return "“studio” is reserved for the studio’s own tools.";
  return null;
}

/** True when every word of `query` is in one of `fields` (case-insensitive). */
export function matchesQuery(fields: readonly (string | null | undefined)[], query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const haystack = fields.filter(Boolean).join(" ").toLowerCase();
  return words.every((word) => haystack.includes(word));
}

/** Where an MCP server lives, in one line: its URL, or its command and arguments. */
export function mcpTarget(server: Pick<McpServer, "transport" | "url" | "command" | "args">): string {
  if (server.transport === "http") return server.url ?? "";
  return [server.command ?? "", ...(server.args ?? [])].filter(Boolean).join(" ");
}

// ---- the project's switches ------------------------------------------------------------

/** The project's library with one switch changed (for an optimistic update). */
export function setEnabled(
  library: ProjectLibrary,
  kind: "skills" | "mcp_servers",
  id: string,
  enabled: boolean,
): ProjectLibrary {
  if (kind === "skills") {
    return { ...library, skills: library.skills.map((item) => (item.id === id ? { ...item, enabled } : item)) };
  }
  return { ...library, mcp_servers: library.mcp_servers.map((item) => (item.id === id ? { ...item, enabled } : item)) };
}

export function enabledMcpCount(library: ProjectLibrary): number {
  return library.mcp_servers.filter((item) => item.enabled).length;
}

/** Whether switching a server on would go over the per-project limit (switching off is always allowed). */
export function mcpLimitReached(library: ProjectLibrary, id: string): boolean {
  const server = library.mcp_servers.find((item) => item.id === id);
  if (!server || server.enabled) return false;
  return enabledMcpCount(library) >= MCP_LIMIT;
}

// ---- the page check result in the activity feed -------------------------------------------

/**
 * `mcp__studio__check_page` answers with JSON; when it parses and has the
 * documented `summary`, say it in one line ("31 verified · 4 derived · 2 failed
 * · 7 unmarked"). Anything else: null, and the raw text is shown as before.
 */
export function checkPageLine(text: string | null | undefined): string | null {
  if (!text) return null;
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  const summary = (data as { summary?: Record<string, unknown> } | null)?.summary;
  if (!summary || typeof summary !== "object") return null;
  const parts: [string, string][] = [
    ["verified", "verified"],
    ["derived", "derived"],
    ["failed", "failed"],
    ["unmarked_numbers", "unmarked"],
  ];
  if (!parts.every(([key]) => typeof summary[key] === "number")) return null;
  return parts.map(([key, word]) => `${summary[key] as number} ${word}`).join(" · ");
}

/**
 * The sections, in the order they are shown. `observedLabel` is how to name
 * `observed_at` in prose (for example a formatted date); it is only used when
 * `observed_at` is set. Built-in skills are never guessed: with no observation
 * they are empty and say when they will appear. MCP servers have their own
 * view (they have actions), so they are not a section here.
 */
export function librarySections(library: Library, observedLabel: string | null = null): LibrarySection[] {
  const observed = library.observed_at !== null;
  const notYet = "This list appears after the first agent run.";
  const toolNames = new Set(
    (library.tools ?? []).flatMap((tool) => [tool.id.toLowerCase(), tool.label.toLowerCase()]),
  );
  const skillRows = (items: LibrarySkill[], namesOnly: boolean): LibraryRow[] =>
    items.map((skill) => ({
      key: `skill:${skill.id}`,
      title: skill.label || skillName(skill),
      detail: namesOnly ? null : skill.description || null,
      status: null,
      note: null,
      action: skill.source === "yours" ? { kind: "delete-skill" as const, name: skillName(skill) } : undefined,
      exportId: EXPORTABLE.has(skill.source) ? skill.id : undefined,
    }));
  // Step 3o: the machine's and plugins' skills are listed under "Everything on this computer".
  const groups = groupSkills(library.skills ?? []).filter((group) => group.source !== "machine" || group.items.length > 0);
  const skillSections: LibrarySection[] = groups.map((group) => {
    const builtIn = group.source === "built-in";
    return {
      id: `skills-${group.source}` as LibrarySection["id"],
      title: group.title,
      intro: builtIn
        ? observed
          ? `${group.intro} As reported by the most recent agent session${observedLabel ? ` (${observedLabel})` : ""}.`
          : null
        : group.intro,
      empty: builtIn
        ? observed
          ? "The last agent session reported no built-in skills."
          : notYet
        : group.source === "yours"
          ? "You haven’t added any skills."
          : group.source === "machine"
            ? "No skills were found in ~/.claude/skills on this computer."
            : "None.",
      rows: skillRows(group.items, builtIn),
    };
  });

  return [
    {
      id: "connections",
      title: "Models & connections",
      intro: null,
      empty: "No connections are configured.",
      rows: (library.connections ?? []).map((connection) => ({
        key: `connection:${connection.id}`,
        title: connection.label,
        detail: connection.models.length ? connection.models.map((model) => model.label).join(", ") : null,
        status: available(connection.available),
        note: connection.available ? null : connection.reason,
      })),
    },
    {
      id: "tools",
      title: "What the agent can do",
      intro: null,
      empty: "The studio reported no tools.",
      rows: (library.tools ?? []).map((tool) => ({
        key: `tool:${tool.id}`,
        title: tool.label,
        detail: tool.description || null,
        status: available(tool.available),
        note: tool.note,
      })),
    },
    ...(library.studio_tools
      ? [
          {
            id: "studio-tools" as const,
            title: "The studio’s own tools",
            intro: "Tools the studio gives the agent in every request.",
            empty: "The studio reported no tools of its own.",
            rows: library.studio_tools
              .filter((tool) => !toolNames.has(tool.name.toLowerCase()))
              .map((tool) => ({
                key: `studio-tool:${tool.name}`,
                title: tool.name,
                detail: tool.description || null,
                status: available(tool.available),
                note: tool.note,
              })),
          },
        ]
      : []),
    ...skillSections,
    ...(library.agents
      ? [
          {
            id: "agents" as const,
            title: "Helper agents",
            intro: "Helpers the agent can hand part of a task to.",
            empty: "The studio comes with no helper agents.",
            rows: library.agents.map((agent) => ({
              key: `agent:${agent.name}`,
              title: agent.name,
              detail: agent.description || null,
              status: null,
              note: null,
            })),
          },
        ]
      : []),
    {
      id: "templates",
      title: "From the studio",
      intro: "Start a new page from one of these.",
      empty: "The studio has no templates.",
      rows: (library.templates ?? []).map((template) => ({
        key: `template:${template.id}`,
        title: template.label,
        detail: template.description || null,
        status: template.app ? { tone: "neutral" as const, text: "Runs its own server" } : null,
        note: null,
      })),
    },
    {
      id: "data-kinds",
      title: "Database types",
      intro: null,
      empty: "The studio lists no database types.",
      rows: (library.data_kinds ?? []).map((kind) => ({
        key: `kind:${kind.kind}`,
        title: kind.label,
        detail: `Default port ${kind.default_port}`,
        status: available(kind.available, "Driver not installed"),
        note: kind.note,
      })),
    },
  ];
}

export function countRows(sections: LibrarySection[]): number {
  return sections.reduce((sum, section) => sum + section.rows.length, 0);
}

/** Keep the rows whose words contain every word of `query` (case-insensitive). Sections stay, possibly empty. */
export function filterSections(sections: LibrarySection[], query: string): LibrarySection[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return sections;
  return sections.map((section) => ({
    ...section,
    rows: section.rows.filter((row) => {
      const haystack = [section.title, row.title, row.detail, row.note, row.status?.text]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return words.every((word) => haystack.includes(word));
    }),
  }));
}

// ---- what the Library opens on, and what is under "Everything else" -------------------------

/** The sections the Library opens on, in this order (Skills, then Templates; Connectors sit between them). */
export const MAIN_SECTIONS: readonly LibrarySection["id"][] = ["skills-studio", "skills-yours", "templates"];

/** Connectors shown up front: the person's own, the studio's suggestions and an attached app's. */
export const MAIN_MCP_SOURCES: readonly string[] = ["yours", "suggested", "app"];

/** The closed section at the end of the Library. */
export const MORE_TITLE = "Everything else";

/** Where the Library remembers, in this browser, whether "Everything else" is open. */
export const MORE_STORAGE_KEY = "library.more";

/** Split the sections into what the Library opens on and what is under "Everything else" (order kept). */
export function splitSections(sections: readonly LibrarySection[]): { main: LibrarySection[]; more: LibrarySection[] } {
  const main = MAIN_SECTIONS.flatMap((id) => sections.filter((section) => section.id === id));
  const more = sections.filter((section) => !MAIN_SECTIONS.includes(section.id));
  return { main, more };
}

/** Split connectors the same way: the person's own and the suggested ones first; the rest found on this computer. */
export function splitServers<T extends McpServer>(servers: readonly T[]): { main: T[]; more: T[] } {
  return {
    main: servers.filter((server) => MAIN_MCP_SOURCES.includes(server.source)),
    more: servers.filter((server) => !MAIN_MCP_SOURCES.includes(server.source)),
  };
}

export type IndexKind = "skills" | "connectors" | "agents";

/** Sources of the index whose items the Library already shows up front. */
const SHOWN_FIRST: Record<IndexKind, readonly string[]> = {
  skills: ["studio", "yours"],
  connectors: MAIN_MCP_SOURCES,
  agents: [],
};

/** Of `counts` (items per source, one kind), how many are only in "Everything else". */
export function countElsewhere(kind: IndexKind, counts: Record<string, number> | null | undefined): number {
  return Object.entries(counts ?? {}).reduce((sum, [source, n]) => (SHOWN_FIRST[kind].includes(source) ? sum : sum + n), 0);
}

/** Skills, connectors and helper agents found on this computer that the Library does not show up front. */
export function elsewhereCounts(index: LibraryIndex | null | undefined): Record<IndexKind, number> {
  const by = index?.by_source;
  return {
    skills: countElsewhere("skills", by?.skills),
    connectors: countElsewhere("connectors", by?.connectors),
    agents: countElsewhere("agents", by?.agents),
  };
}

/** A block of "Everything else" that is not a list of rows (Models, Keys, …): found by its title and a few words. */
export interface MoreBlockWords {
  title: string;
  words: string;
}

/** Whether a search reaches a block: every word of `query` is in its title or words. */
export function blockMatches(block: MoreBlockWords, query: string): boolean {
  return query.trim() !== "" && matchesQuery([block.title, block.words], query);
}

/**
 * How many things a search finds in "Everything else": the rows of its sections, its connectors,
 * its blocks matched by name, and what the index of this computer finds outside the front.
 */
export function moreMatchCount(input: {
  sections: readonly LibrarySection[];
  servers: number;
  blocks: readonly MoreBlockWords[];
  query: string;
  index?: Partial<Record<IndexKind, number>> | null;
}): number {
  if (!input.query.trim()) return 0;
  const rows = countRows(filterSections([...input.sections], input.query));
  const blocks = input.blocks.filter((block) => blockMatches(block, input.query)).length;
  const index = Object.values(input.index ?? {}).reduce((sum, n) => sum + (n ?? 0), 0);
  return rows + input.servers + blocks + index;
}

/** The first kind the index found something for, so its list opens on that kind. */
export function firstKindFound(hits: Partial<Record<IndexKind, number>> | null | undefined): IndexKind | null {
  for (const kind of ["skills", "connectors", "agents"] as const) if ((hits?.[kind] ?? 0) > 0) return kind;
  return null;
}

/** "1 skill", "3 connectors", "2 helper agents". */
export function countWords(n: number, kind: IndexKind): string {
  const noun = { skills: "skill", connectors: "connector", agents: "helper agent" }[kind];
  return `${n.toLocaleString("en-US")} ${noun}${n === 1 ? "" : "s"}`;
}
