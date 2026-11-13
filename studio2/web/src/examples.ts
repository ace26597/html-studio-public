/** Examples that come with the studio (CONTRACT.md): finished pages a new install can open as a copy. */

export interface Example {
  id: string;
  title: string;
  summary: string;
  kind: "report" | "dashboard" | "explainer" | "interactive";
  made: {
    model: string | null;
    date: string | null;
    requests: number | null;
    duration_s: number | null;
    depth: string | null;
    request: string | null;
  };
  sources: number | null;
  check: { checked: number; passed: number; fixed: number; failed: number; unverifiable: number } | null;
  files: number;
  bytes: number;
  preview: string | null;
}

export const KIND_LABEL: Record<Example["kind"], string> = {
  report: "Report",
  dashboard: "Dashboard",
  explainer: "Explainer",
  interactive: "Interactive",
};

/** "opus" → "Opus"; "openrouter/openai/gpt-6.1-sol" → "gpt-6.1-sol". */
export function modelName(model: string | null): string | null {
  if (!model) return null;
  const last = model.split("/").pop() ?? model;
  return /^(opus|sonnet|haiku)$/.test(last) ? last[0].toUpperCase() + last.slice(1) : last;
}

function minutes(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} s`;
  return `${Math.round(seconds / 60)} min`;
}

/** One line of facts for a card, only from what the example's record holds: nothing is filled in. */
export function exampleFacts(example: Example): string {
  const { made } = example;
  const parts: string[] = [];
  const model = modelName(made.model);
  if (model) parts.push(model);
  if (made.depth === "quick") parts.push("Quick");
  if (made.depth === "deep") parts.push("Deep");
  if (typeof made.duration_s === "number") parts.push(minutes(made.duration_s));
  if (typeof made.requests === "number" && made.requests > 1) parts.push(`${made.requests} requests`);
  if (typeof example.sources === "number" && example.sources > 0) {
    parts.push(`${example.sources} ${example.sources === 1 ? "source" : "sources"}`);
  }
  if (example.check) parts.push(`${example.check.checked} figures checked`);
  return parts.join(" · ");
}
