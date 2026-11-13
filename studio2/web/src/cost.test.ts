import { describe, expect, it } from "vitest";
import { canRecover, costRows, pendingNote, recoveryMessage, turnAmount } from "./cost";
import type { CostTurn, ProjectCost } from "./types";

const turn = (extra: Partial<CostTurn>): CostTurn => ({
  turn_id: "t_1",
  at: "2026-11-12T19:00:22.874Z",
  connection: "litellm",
  model: "openrouter/openai/gpt-6.1-sol",
  kind: "charged",
  cost_usd: null,
  basis: null,
  requests: 0,
  unpriced_requests: 0,
  source: null,
  reason: null,
  ...extra,
});

const empty: ProjectCost = {
  charged_usd: 0,
  calculated_usd: 0,
  plan_equivalent_usd: 0,
  unpriced_turns: 0,
  unpriced_requests: 0,
  pending_turns: 0,
  turns: [],
};

describe("costRows", () => {
  it("shows no figure at all for a page that has not used a model", () => {
    expect(costRows(empty).map((row) => row.value)).toEqual(["—", "—", "—"]);
  });

  it("keeps charged, plan-equivalent and unpriced apart", () => {
    const rows = costRows({
      calculated_usd: 0,
      charged_usd: 4.546269,
      plan_equivalent_usd: 1.52,
      unpriced_turns: 2,
      unpriced_requests: 4,
      pending_turns: 0,
      turns: [turn({ kind: "charged", cost_usd: 4.546269 }), turn({ kind: "plan", cost_usd: 1.52 }), turn({ kind: "unpriced" })],
    });
    expect(rows.map((row) => [row.key, row.value])).toEqual([
      ["charged", "$4.55"],
      ["plan", "≈ $1.52"],
      ["unpriced", "2 turns · 4 requests"],
    ]);
    expect(rows[1].note).toContain("Not a charge");
  });

  it("does not turn an unpriced-only page into a zero charge", () => {
    const rows = costRows({ ...empty, unpriced_turns: 1, unpriced_requests: 3, turns: [turn({ kind: "unpriced" })] });
    expect(rows[0].value).toBe("—");
    expect(rows[2].value).toBe("1 turn · 3 requests");
  });
});

describe("where a figure is from", () => {
  it("says how much of the charged total was calculated from a price list", () => {
    const rows = costRows({ ...empty, charged_usd: 5, calculated_usd: 0.84, turns: [turn({ kind: "charged", cost_usd: 5 })] });
    expect(rows[0].note).toContain("$0.84 is calculated");
    expect(costRows({ ...empty, charged_usd: 5, turns: [turn({ kind: "charged", cost_usd: 5 })] })[0].note).not.toContain("calculated");
  });

  it("marks a calculated turn and leaves a reported one plain", () => {
    expect(turnAmount(turn({ kind: "charged", cost_usd: 0.84, basis: "calculated" }))).toBe("$0.84 calculated");
    expect(turnAmount(turn({ kind: "charged", cost_usd: 0.84, basis: "reported" }))).toBe("$0.84");
  });
});

describe("turnAmount", () => {
  it("words each kind and says why there is no figure", () => {
    expect(turnAmount(turn({ kind: "charged", cost_usd: 0.84, unpriced_requests: 1 }))).toBe("$0.84 + 1 unpriced");
    expect(turnAmount(turn({ kind: "charged", cost_usd: 4.446269, source: "recovered" }))).toBe(
      "$4.45 · found by session and time",
    );
    expect(turnAmount(turn({ kind: "plan", cost_usd: 1.02 }))).toBe("≈ $1.02 at API prices");
    expect(turnAmount(turn({ kind: "pending" }))).toBe("cost pending");
    expect(turnAmount(turn({ kind: "unpriced", reason: "The gateway has no spend log." }))).toBe(
      "no price · The gateway has no spend log.",
    );
    expect(turnAmount(turn({ kind: "unpriced" }))).toBe("no price reported");
  });
});

describe("the rest", () => {
  it("notes waiting turns", () => {
    expect(pendingNote(empty)).toBeNull();
    expect(pendingNote({ ...empty, pending_turns: 1 })).toContain("1 turn is waiting");
    expect(pendingNote({ ...empty, pending_turns: 2 })).toContain("2 turns are waiting");
  });

  it("offers the lookup only when a turn has no price", () => {
    expect(canRecover(empty)).toBe(false);
    expect(canRecover({ ...empty, turns: [turn({ kind: "charged", cost_usd: 1 })] })).toBe(false);
    expect(canRecover({ ...empty, turns: [turn({ kind: "unpriced" })] })).toBe(true);
  });

  it("reports what a lookup found and what it could not", () => {
    expect(recoveryMessage({ recovered: 1, unmatched: 0, reason: null })).toBe(
      "Priced 1 turn from the gateway's log, by session and time.",
    );
    expect(recoveryMessage({ recovered: 2, unmatched: 1, reason: null })).toContain("1 turn could not be matched");
    expect(recoveryMessage({ recovered: 0, unmatched: 1, reason: "The gateway has no spend log." })).toBe(
      "The gateway has no spend log.",
    );
    expect(recoveryMessage({ recovered: 0, unmatched: 0, reason: null })).toContain("No unpriced turn");
  });
});
