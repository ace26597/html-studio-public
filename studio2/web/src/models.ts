// Models and “Check this model” (step 3n): types, API calls and the pure wording.
// CONTRACT.md, "Any model (step 3n)".

import { request } from "./api";
import { costLabel, formatCount, formatDuration } from "./format";
import type { LitellmStatus, OtherModelsNote } from "./setup";
import type { Connection, ModelOption } from "./types";

export interface CheckPart {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
}

export interface CheckResult {
  connection: string;
  model: string;
  status: "passed" | "failed" | "stopped";
  /** The first part that failed, in a sentence; null when it passed. */
  reason: string | null;
  parts: CheckPart[];
  seconds: number;
  /** Tool calls made across both messages. */
  steps: number;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  cost_estimated: boolean;
  notes: string[];
  trace: { message: number | null; name: string; title: string; status: string | null }[];
  said: string;
  at: string;
}

/** Whether a request on a model costs money, as far as the studio can tell. */
export type ModelCost = "free" | "plan" | "paid" | "unknown";

export interface CheckedModel extends ModelOption {
  cost: ModelCost;
  /** What to say before a check that may cost something; null when it cannot. */
  warning: string | null;
  check: CheckResult | null;
}

export interface ModelsConnection extends Omit<Connection, "models"> {
  provider: string | null;
  small_model: string | null;
  /** The model of this connection that picks skills for its requests; null when it has none (word search). */
  picking_model?: string | null;
  models: CheckedModel[];
}

export interface RunningCheck {
  connection: string;
  model: string;
  started_at: string;
  phase: string;
  stopping?: boolean;
}

export interface ModelsInfo {
  connections: ModelsConnection[];
  running: RunningCheck | null;
  /** The five things the check verifies, in order. */
  parts: string[];
  check_version: number;
  note: OtherModelsNote;
  litellm: LitellmStatus;
}

export const modelsApi = {
  get: (signal?: AbortSignal) => request<ModelsInfo>("GET", "/models", undefined, signal),
  check: (connection: string, model: string, confirmCost: boolean) =>
    request<RunningCheck>("POST", "/models/check", { connection, model, confirm_cost: confirmCost }),
  stop: () => request<{ stopping: boolean }>("POST", "/models/check/stop"),
};

export type Tone = "ok" | "off" | "neutral";

/** The chip beside a model: "Passed", "4 of 5 passed", "Failed", "Stopped", "Not checked". */
export function checkChip(result: CheckResult | null): { tone: Tone; text: string } {
  if (!result) return { tone: "neutral", text: "Not checked" };
  if (result.status === "passed") return { tone: "ok", text: "Passed" };
  if (result.status === "stopped") return { tone: "neutral", text: "Stopped" };
  const passed = result.parts.filter((part) => part.ok).length;
  if (passed > 0 && passed < result.parts.length) return { tone: "off", text: `${passed} of ${result.parts.length} passed` };
  return { tone: "off", text: "Failed" };
}

/** "12 Nov, 5:38 PM" in the reader's own locale; empty for a date that cannot be read. */
export function checkedWhen(iso: string, locale?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(locale, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

/** The facts under a model's name: "48s · 10 steps · $0.10 · 161k tokens · 12 Nov, 5:38 PM". */
export function checkFacts(result: CheckResult, locale?: string): string {
  const facts = [formatDuration(result.seconds * 1000), `${result.steps} step${result.steps === 1 ? "" : "s"}`];
  const cost = costLabel({ cost_usd: result.cost_usd, cost_estimated: result.cost_estimated });
  if (cost) facts.push(cost.text);
  const tokens = (result.input_tokens ?? 0) + (result.output_tokens ?? 0);
  if (tokens > 0) facts.push(`${formatCount(tokens)} tokens`);
  const when = checkedWhen(result.at, locale);
  if (when) facts.push(when);
  return facts.join(" · ");
}

/** What a model costs to use, in two or three words; null where there is nothing to say. */
export function costWords(cost: ModelCost): string | null {
  if (cost === "free") return "Free";
  if (cost === "plan") return "On your Claude plan";
  if (cost === "paid") return "Billed per use";
  return "May cost money";
}

export function isRunning(info: ModelsInfo | null, connection: string, model: string): boolean {
  return info?.running?.connection === connection && info.running.model === model;
}

/** Whether the note about models that are not Claude's applies to anything listed. */
export function hasOtherModels(info: ModelsInfo): boolean {
  return info.connections.some((connection) => connection.models.some((model) => model.experimental));
}

/** Whether the studio's own LiteLLM is worth a line: something needs it, or it is there. */
export function showsLitellm(litellm: LitellmStatus): boolean {
  return litellm.needed || litellm.installed || litellm.installing || litellm.install_error !== null;
}

/** One line for the Models block: which model picks skills on this connection, or that none is set. */
export function pickingLine(connection: { kind: string; picking_model?: string | null }): string {
  if (connection.picking_model) return `Skills are picked by ${connection.picking_model}, on this connection.`;
  if (connection.kind === "fake") return "";
  return "No cheap model for picking skills: the word search decides. Add small_model to this connection in setup.";
}
