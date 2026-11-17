/** Suggested next steps (CONTRACT.md): chips under the last answer. A tap fills the chat box; nothing is sent. */

import { DEPTHS, type Depth, type TurnOptions } from "./effort";
import type { Turn } from "./thread";

export interface NextStep {
  id: string;
  from: "studio" | "agent";
  /** What the chip says. */
  label: string;
  /** What lands in the chat box. */
  text: string;
  /** Sent with the message only when the person sends `text` unchanged. */
  options?: TurnOptions;
}

function optionsOf(raw: unknown): TurnOptions | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const value = raw as Record<string, unknown>;
  if (!DEPTHS.some((d) => d.id === value.depth) || typeof value.verify !== "boolean") return undefined;
  const options: TurnOptions = { depth: value.depth as Depth, verify: value.verify };
  if (value.check_only === true) options.check_only = true;
  return options;
}

/** A `next_steps` event → its steps; anything without an id, a label and a text is dropped. */
export function stepsFrom(event: Record<string, unknown>): NextStep[] {
  if (!Array.isArray(event.steps)) return [];
  const steps: NextStep[] = [];
  for (const raw of event.steps) {
    if (!raw || typeof raw !== "object") continue;
    const item = raw as Record<string, unknown>;
    if (typeof item.id !== "string" || typeof item.label !== "string" || typeof item.text !== "string") continue;
    if (!item.id || !item.label.trim() || !item.text.trim()) continue;
    const step: NextStep = {
      id: item.id,
      from: item.from === "studio" ? "studio" : "agent",
      label: item.label,
      text: item.text,
    };
    const options = optionsOf(item.options);
    if (options) step.options = options;
    steps.push(step);
  }
  return steps;
}

/** The chips to show under a request: only the last one, once it completed, with nothing waiting. */
export function shownSteps(turn: Turn, isLast: boolean, busy = false): NextStep[] {
  if (!isLast || busy || turn.status !== "completed") return [];
  return turn.nextSteps ?? [];
}

/** The chat box after a tap: the step's text, after what is already typed (never instead of it). */
export function fillDraft(draft: string, text: string): string {
  const typed = draft.trimEnd();
  if (!typed) return text;
  if (typed === text || typed.endsWith(`\n${text}`)) return typed;
  return `${typed}\n${text}`;
}

/** True when the chat box already holds this text (as typed, or put back earlier). */
export function draftHolds(draft: string, text: string): boolean {
  const wanted = text.trim();
  return wanted !== "" && draft.includes(wanted);
}

/** "Put it back": the text after what is typed, once; a box that already holds it is left as it is. */
export function putBackDraft(draft: string, text: string): string {
  return draftHolds(draft, text) ? draft : fillDraft(draft, text);
}

/** The options a message goes with when it is exactly a shown step's text; otherwise undefined. */
export function optionsForText(steps: readonly NextStep[], typed: string): TurnOptions | undefined {
  const text = typed.trim();
  return steps.find((step) => step.options && step.text === text)?.options;
}
