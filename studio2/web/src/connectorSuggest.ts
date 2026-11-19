// A connector from a description (CONTRACT.md, the section of that name): how a model's suggestion
// fills the form, and how what the studio checked is said. All pure.

import { relativeTime } from "./format";
import { emptyMcpForm, secretRow, type McpForm } from "./mcpServerForm";
import type { McpSuggestAnswer, McpSuggestion, SuggestionCheck } from "./types";

/** The most characters a description may have. */
export const MAX_SUGGEST_TEXT = 2_000;

export const SUGGEST_EXAMPLE = "Read and search the pages in my Notion workspace";

/** Below this many downloads a week a package is said to be little used. */
const FEW_DOWNLOADS = 1_000;
const NEW_DAYS = 30;
const DAY = 86_400_000;

/** Why Suggest cannot be pressed yet, or null. */
export function suggestProblem(text: string): string | null {
  const said = text.trim();
  if (!said) return "Say what you want the agent to reach or do.";
  if (said.length > MAX_SUGGEST_TEXT) return `The description can be at most ${MAX_SUGGEST_TEXT.toLocaleString("en-US")} characters.`;
  return null;
}

/** The form, filled from a suggestion: one empty row per key, with where its value comes from. */
export function formFromSuggestion(suggestion: McpSuggestion): McpForm {
  const http = suggestion.transport !== "stdio";
  return {
    ...emptyMcpForm(),
    name: suggestion.name,
    label: suggestion.label ?? "",
    transport: http ? "http" : "stdio",
    url: http ? (suggestion.url ?? "") : "",
    command: http ? "" : (suggestion.command ?? ""),
    args: http ? "" : (suggestion.args ?? []).join("\n"),
    secrets: (suggestion.secrets ?? []).map((secret) => secretRow(secret.name, false, secret.where || undefined)),
    description: suggestion.description ?? "",
  };
}

/** Its address, or its command and arguments, in one line. */
export function suggestionTarget(suggestion: Pick<McpSuggestion, "transport" | "url" | "command" | "args">): string {
  if (suggestion.transport !== "stdio") return suggestion.url ?? "";
  return [suggestion.command ?? "", ...(suggestion.args ?? [])].filter(Boolean).join(" ");
}

/** "2 days ago", "3 weeks ago", "4 months ago", "2 years ago". */
export function ageWords(iso: string, now: number): string {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const days = Math.floor(Math.max(0, now - then) / DAY);
  if (days < 7) return relativeTime(iso, now);
  if (days < 60) {
    const weeks = Math.floor(days / 7);
    return `${weeks} ${weeks === 1 ? "week" : "weeks"} ago`;
  }
  if (days < 730) return `${Math.floor(days / 30)} months ago`;
  return `${Math.floor(days / 365)} years ago`;
}

const REGISTRY: Record<string, string> = { npm: "npm", pypi: "PyPI" };

/** What the studio checked, in one line. */
export function checkLine(check: SuggestionCheck | null | undefined, now: number = Date.now()): string {
  if (!check) return "Not checked";
  if (check.kind === "address") {
    if (check.found === null) return "Not checked";
    if (check.found === false) return `The address did not answer${check.status ? ` (HTTP ${check.status})` : ""}`;
    if (check.status === 401 || check.status === 403) {
      return `The address answered and wants a key or a sign-in (HTTP ${check.status})`;
    }
    return check.status ? `The address answered (HTTP ${check.status})` : "The address answered";
  }
  const registry = REGISTRY[check.kind] ?? check.kind;
  if (check.found === null) return `${registry} could not be reached, so this package was not checked`;
  if (check.found === false) return `${check.package} was not found on ${registry}`;
  let line = `On ${registry}: ${check.package}`;
  if (check.version) line += ` ${check.version}`;
  const age = check.published ? ageWords(check.published, now) : "";
  if (age) line += `, published ${age}`;
  if (typeof check.downloads_week === "number") line += `, ${check.downloads_week.toLocaleString("en-US")} downloads last week`;
  return line;
}

/** A sentence when the check gives a reason to look twice, else null. */
export function checkCaution(check: SuggestionCheck | null | undefined, now: number = Date.now()): string | null {
  if (!check || check.found === null) return "This could not be checked. Make sure it is the real one before you add it.";
  if (check.kind === "address") return null;
  const downloads = check.downloads_week;
  const few = typeof downloads === "number" && downloads < FEW_DOWNLOADS;
  const firstAt = check.first_published ? Date.parse(check.first_published) : NaN;
  const fresh = Number.isFinite(firstAt) && now - firstAt < NEW_DAYS * DAY;
  if (fresh) {
    const use = few ? `, and few people use it yet (${(downloads as number).toLocaleString("en-US")} downloads last week)` : "";
    return `This package is new: its first version was published ${ageWords(check.first_published as string, now)}${use}. A name a model remembers can belong to someone else, so read its page before you add it.`;
  }
  if (few) {
    return `Few people use this package (${(downloads as number).toLocaleString("en-US")} downloads last week). Read its page before you add it.`;
  }
  return null;
}

/** For a connector that signs in through a browser: add it to the agent program, then sign in. */
export function signInCommands(suggestion: Pick<McpSuggestion, "name" | "url">): [string, string] {
  return [
    `claude mcp add --scope user --transport http ${suggestion.name} ${suggestion.url ?? ""}`.trimEnd(),
    `claude mcp login ${suggestion.name}`,
  ];
}

export const SIGN_IN_NOTE =
  "A connector added in the studio cannot sign in through a browser, so this one is added to the agent program with these commands, and then appears in the Library among this computer’s.";

/** "Suggested by <model>, from memory". */
export function suggestedHeading(asked: McpSuggestAnswer["asked"]): string {
  return `Suggested by ${asked?.model || "a model"}, from memory`;
}

/** "Left out: A (reason); B (reason)", or null. */
export function leftOutLine(leftOut: McpSuggestAnswer["left_out"]): string | null {
  if (!leftOut?.length) return null;
  return `Left out: ${leftOut.map((item) => `${item.label} (${item.reason})`).join("; ")}`;
}

/** The note above a form a suggestion filled. */
export function suggestedFormNote(suggestion: McpSuggestion, model: string | null | undefined, now: number = Date.now()): string {
  return `Suggested by ${model || "a model"} from memory. ${checkLine(suggestion.check, now)}. Nothing is added until you press Add.`;
}

/** Nothing suggested and nothing in the library: the answer says so and offers the form. */
export function answerIsEmpty(answer: McpSuggestAnswer): boolean {
  return answer.suggestions.length === 0 && answer.have.length === 0;
}
