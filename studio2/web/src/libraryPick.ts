// Step 3o: the words and small rules behind the library screens and the "picked" card.
// Pure functions, so the node test environment can check them.

import { api } from "./api";
import type {
  ConnectorState,
  LibraryIndex,
  LibraryItem,
  LibraryItemsPage,
  LibraryPick,
  MachinePref,
  ProjectChoice,
} from "./types";

/** The three-way control. */
export type Decision = "auto" | "always" | "never";
/** Where a decision is kept: this project, or the whole computer. */
export type Scope = "project" | "machine";
/** Which list a decision is about (the REST bodies use these keys). */
export type DecisionKind = "skills" | "mcp_servers";

export const PAGE_SIZE = 50;
export const PROJECT_PAGE_SIZE = 20;

const number = new Intl.NumberFormat("en-US");
export const fmt = (n: number) => number.format(n);

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${fmt(n)} ${n === 1 ? one : many}`;
}

// ---- decisions ---------------------------------------------------------------------------

export function fromChoice(choice: ProjectChoice | undefined): Decision {
  return choice === true ? "always" : choice === false ? "never" : "auto";
}

export function fromPref(pref: MachinePref | undefined): Decision {
  return pref === "always" || pref === "never" ? pref : "auto";
}

export function toChoice(decision: Decision): ProjectChoice {
  return decision === "always" ? true : decision === "never" ? false : null;
}

export function toPref(decision: Decision): MachinePref {
  return decision === "auto" ? null : decision;
}

/** The request body for one decision: the project's PUT takes true/false/null, the machine's always/never/null. */
export function decisionBody(kind: DecisionKind, id: string, scope: Scope, decision: Decision) {
  const value = scope === "project" ? toChoice(decision) : toPref(decision);
  return { [kind]: { [id]: value } } as Record<DecisionKind, Record<string, ProjectChoice | MachinePref>>;
}

/** Save one decision through the right endpoint. Resolves with the server's answer. */
export function saveDecision(
  kind: DecisionKind,
  id: string,
  scope: Scope,
  decision: Decision,
  projectId?: string | null,
) {
  if (scope === "project") {
    if (!projectId) return Promise.reject(new Error("No project to save this in."));
    return api.putProjectLibrary(projectId, { [kind]: { [id]: toChoice(decision) } });
  }
  return api.putLibraryPrefs({ [kind]: { [id]: toPref(decision) } });
}

/** An item with one decision changed (for optimistic updates and their roll back). */
export function withDecision<T extends { choice?: ProjectChoice; pref?: MachinePref }>(
  item: T,
  scope: Scope,
  decision: Decision,
): T {
  return scope === "project" ? { ...item, choice: toChoice(decision) } : { ...item, pref: toPref(decision) };
}

/** What was saved, in words: "Always in this project", "Never on this computer", "Left to the studio". */
export function savedWords(scope: Scope, decision: Decision): string {
  if (decision === "auto") return "Left to the studio";
  const where = scope === "project" ? "in this project" : "on this computer";
  return `${decision === "always" ? "Always" : "Never"} ${where}`;
}

// ---- the paged list ------------------------------------------------------------------------

/** Append the next page, or replace the list with a first page. Duplicate ids are dropped. */
export function mergePage(previous: LibraryItem[] | null, page: LibraryItemsPage, append: boolean): LibraryItem[] {
  if (!append || !previous) return page.items;
  const seen = new Set(previous.map((item) => item.id));
  return [...previous, ...page.items.filter((item) => !seen.has(item.id))];
}

/** Rows not fetched yet ("Show more" while > 0). */
export function remaining(loaded: number, total: number): number {
  return Math.max(0, total - loaded);
}

// ---- summary lines ---------------------------------------------------------------------------

/** "510 skills, 60 helper agents and 33 connectors found in 94 plugins and your own folders; index built in 0.7 s". */
export function indexSummary(index: LibraryIndex): string {
  const parts = [plural(index.skills, "skill"), plural(index.agents, "helper agent"), plural(index.connectors, "connector")];
  const found = `${parts[0]}, ${parts[1]} and ${parts[2]}`;
  const where = index.plugins ? `in ${plural(index.plugins, "plugin")} and your own folders` : "in your own folders";
  const built =
    typeof index.build_ms === "number" ? `; index built in ${(index.build_ms / 1000).toFixed(1)} s` : "";
  return `${found} found ${where}${built}`;
}

export function skillFacts(item: LibraryItem): string[] {
  const out: string[] = [];
  if (item.benched) out.push(`No longer picked: chosen ${plural(item.picked ?? 0, "time")} and never used`);
  else if (item.picked || item.used) {
    out.push(`Picked ${plural(item.picked ?? 0, "time")}`, `used ${plural(item.used ?? 0, "time")}`);
  }
  if (item.enabled === false) out.push("plugin switched off in Claude Code");
  return out;
}

export interface StateLine {
  tone: "warn" | "err" | "ok";
  text: string;
  /** A command to copy (signing in). */
  command?: string;
  /** A sentence (a missing setting). */
  sentence?: string;
}

export function connectorState(state: ConnectorState | undefined, fix: string | null | undefined): StateLine | null {
  switch (state) {
    case "needs-auth":
      return { tone: "warn", text: "Needs signing in", command: fix || undefined };
    case "needs-setting":
      return { tone: "warn", text: "Needs a setting", sentence: fix || undefined };
    case "failed":
      return { tone: "err", text: "Could not connect last time" };
    default:
      return null;
  }
}

// ---- the "picked" card ------------------------------------------------------------------------

/** The closed line of the card, or null when nothing was added on top of the studio's own skills. */
export function pickHeadline(pick: LibraryPick): string | null {
  const skills = pick.skills?.length ?? 0;
  const connectors = pick.connectors?.length ?? 0;
  if (!skills && !connectors) return null;
  if (pick.mode === "all") {
    const loaded = pick.loaded_skills ?? skills;
    const extra = connectors ? ` and ${plural(connectors, "connector")}` : "";
    return `Loaded your whole library (${plural(loaded, "skill")})${extra}`;
  }
  const what = [skills ? plural(skills, "skill") : "", connectors ? plural(connectors, "connector") : ""]
    .filter(Boolean)
    .join(" and ");
  return `Picked ${what} for this request`;
}

export function pickModeWords(mode: string): string {
  switch (mode) {
    case "model":
      return "chosen by the fast model";
    case "words":
      return "chosen by word search";
    case "all":
      return "your whole library was loaded";
    default:
      return "nothing was picked";
  }
}

export function howWords(how: string): string {
  switch (how) {
    case "pinned":
      return "you said always";
    case "earlier":
      return "used earlier in this project";
    case "requested":
      return "asked for by the assistant";
    case "all":
      return "whole library";
    case "picked":
      return "picked for this request";
    default:
      return how;
  }
}

/** "7,412 of 8,000 characters", or null when the event has no sizes. */
export function listSize(pick: LibraryPick): string | null {
  if (typeof pick.list_chars !== "number" || typeof pick.budget_chars !== "number") return null;
  return `${fmt(pick.list_chars)} of ${fmt(pick.budget_chars)} characters`;
}

/** "2.4 s" for the whole pick (copying included), or null. */
export function pickTime(pick: LibraryPick): string | null {
  const ms = typeof pick.total_ms === "number" ? pick.total_ms : pick.ms;
  if (typeof ms !== "number") return null;
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** Normalise a raw event (or a stored `last_pick`) so rendering never trips on a missing list. */
export function asPick(raw: unknown): LibraryPick | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Partial<LibraryPick>;
  return {
    ...(value as LibraryPick),
    mode: typeof value.mode === "string" ? value.mode : "none",
    skills: Array.isArray(value.skills) ? value.skills : [],
    connectors: Array.isArray(value.connectors) ? value.connectors : [],
    note: typeof value.note === "string" && value.note ? value.note : null,
  };
}
