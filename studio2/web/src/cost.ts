// What a page's requests cost, as the dialog words it. Pure; the dialog only draws these.
// A price is only ever one a route reported: nothing here works a figure out from tokens, and a
// request nobody priced is never shown as free (CONTRACT.md, "Cost per request and per project").

import { backupTip, CALCULATED_TIP, ESTIMATE_TIP, formatCost, RECOVERED_TIP, UNPRICED_TIP } from "./format";
import type { CostRecovery, CostTurn, ProjectCost } from "./types";

export interface CostRow {
  key: "charged" | "plan" | "unpriced";
  label: string;
  value: string;
  note: string;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** How much of the charged total is not the provider's own figure, and whose arithmetic it is. */
function chargedNote(cost: ProjectCost): string {
  const backup = cost.backup_usd ?? 0;
  const parts: string[] = [];
  if (cost.calculated_usd > 0) {
    parts.push(`${formatCost(cost.calculated_usd)} is calculated from token counts and a price list`);
  }
  if (backup > 0) {
    parts.push(`${formatCost(backup)} was worked out by the studio as a backup, for requests that came back with no cost`);
  }
  if (parts.length === 0) return "What the routes that bill reported for these requests.";
  return `Of this, ${parts.join(", and ")}; the rest is what the provider reported.`;
}

/** The three totals. They are never added together. */
export function costRows(cost: ProjectCost): CostRow[] {
  const has = (kind: CostTurn["kind"]) => cost.turns.some((turn) => turn.kind === kind);
  const unpriced = cost.unpriced_turns > 0 || cost.unpriced_requests > 0;
  return [
    {
      key: "charged",
      label: "Charged",
      value: has("charged") ? formatCost(cost.charged_usd) : "—",
      note: chargedNote(cost),
    },
    {
      key: "plan",
      label: "Plan-equivalent",
      value: has("plan") ? `≈ ${formatCost(cost.plan_equivalent_usd)}` : "—",
      note: `${ESTIMATE_TIP}. Not part of the charged total.`,
    },
    {
      key: "unpriced",
      label: "Unpriced",
      value: unpriced
        ? [
            cost.unpriced_turns ? plural(cost.unpriced_turns, "turn", "turns") : "",
            cost.unpriced_requests ? plural(cost.unpriced_requests, "request", "requests") : "",
          ]
            .filter(Boolean)
            .join(" · ")
        : "—",
      note: `${UNPRICED_TIP} No price is worked out from token counts.`,
    },
  ];
}

export function pendingNote(cost: ProjectCost): string | null {
  if (cost.pending_turns <= 0) return null;
  return `${plural(cost.pending_turns, "turn is", "turns are")} waiting for the gateway's spend log, which it writes a few seconds late.`;
}

/** One turn in the list: what it cost, or why there is no figure. */
export function turnAmount(turn: CostTurn): string {
  if (turn.kind === "pending") return "cost pending";
  if (turn.kind === "plan") return `≈ ${formatCost(turn.cost_usd ?? 0)} at API prices`;
  if (turn.kind === "charged") {
    const more = turn.unpriced_requests > 0 ? ` + ${turn.unpriced_requests} unpriced` : "";
    const how = turn.basis === "backup" ? " calculated by the studio" : turn.basis === "calculated" ? " calculated" : "";
    return `${formatCost(turn.cost_usd ?? 0)}${how}${more}${turn.source === "recovered" ? " · found by session and time" : ""}`;
  }
  return turn.reason ? `no price · ${turn.reason}` : "no price reported";
}

export function turnTip(turn: CostTurn): string | null {
  if (turn.kind === "plan") return ESTIMATE_TIP;
  if (turn.basis === "backup" && turn.backup) return backupTip(turn.backup);
  if (turn.basis === "calculated") return CALCULATED_TIP;
  if (turn.source === "recovered") return RECOVERED_TIP;
  return null;
}

/** Whether a lookup in the gateway's log could price something: only turns with no price. */
export function canRecover(cost: ProjectCost): boolean {
  return cost.turns.some((turn) => turn.kind === "unpriced");
}

export function recoveryMessage(result: CostRecovery): string {
  if (result.recovered > 0) {
    const missed = result.unmatched > 0 ? ` ${plural(result.unmatched, "turn", "turns")} could not be matched.` : "";
    return `Priced ${plural(result.recovered, "turn", "turns")} from the gateway's log, by session and time.${missed}`;
  }
  return result.reason || "No unpriced turn could be matched in the gateway's log.";
}
