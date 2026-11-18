import { describe, expect, it } from "vitest";
import {
  DEPTHS,
  depthLine,
  projectOptions,
  stepDepth,
  verificationHeadline,
  verificationItem,
  verificationTone,
} from "./effort";
import { initialThread, reduceEvents, type Turn } from "./thread";
import type { StudioEvent } from "./types";

const ev = (seq: number, e: Record<string, unknown>) =>
  ({ seq, ts: `2026-11-13T09:00:0${seq}Z`, turn_id: "t_a", ...e }) as unknown as StudioEvent;

describe("depth and verify options", () => {
  it("reads the remembered choice, anything odd as the default", () => {
    expect(projectOptions(undefined)).toEqual({ depth: "standard", verify: false });
    expect(projectOptions({ depth: "deep", verify: true })).toEqual({ depth: "deep", verify: true });
    expect(projectOptions({ depth: "huge", verify: "yes" })).toEqual({ depth: "standard", verify: false });
  });

  it("says what each step means, and that Deep costs more", () => {
    expect(DEPTHS.map((d) => d.label)).toEqual(["Quick", "Standard", "Deep"]);
    expect(depthLine("deep")).toMatch(/longer/);
    expect(depthLine("deep")).toMatch(/allowance or money/);
    expect(depthLine("quick")).toMatch(/smallest/);
  });

  it("moves along the steps with the keyboard and stops at the ends", () => {
    expect(stepDepth("standard", "ArrowRight")).toBe("deep");
    expect(stepDepth("deep", "ArrowRight")).toBe("deep");
    expect(stepDepth("standard", "ArrowLeft")).toBe("quick");
    expect(stepDepth("deep", "Home")).toBe("quick");
    expect(stepDepth("quick", "Enter")).toBeNull();
  });
});

describe("the verification card", () => {
  const event = {
    checked: 5, passed: 2, fixed: 1, failed: 1, unverifiable: 1, summary: "s", findings_cut: 2,
    findings: [
      { what: "a", where: "p", status: "passed", note: "" },
      { what: "b", where: "p", status: "fixed", note: "" },
      { what: "c", where: "p", status: "failed", note: "x" },
      { what: "d", where: "p", status: "unverifiable", note: "" },
    ],
  };

  it("puts failed findings first and counts first in the headline", () => {
    const item = verificationItem(event, "k", "ts");
    expect(item.findings.map((f) => f.status)).toEqual(["failed", "unverifiable", "fixed", "passed"]);
    expect(verificationHeadline(item)).toBe(
      "Checked 5 · 1 wrong and not fixed · 1 fixed · 1 could not be checked · 2 matched",
    );
    expect(verificationTone(item)).toBe("error");
    expect(item.cut).toBe(2);
  });

  it("never reads a broken event as a pass", () => {
    const item = verificationItem({ checked: -3, findings: [{ what: "x", status: "weird" }] }, "k", "ts");
    expect(item.checked).toBe(0);
    expect(item.findings[0].status).toBe("unverifiable");
    expect(verificationTone(item)).toBe("ok");
    expect(verificationTone(verificationItem({ unverifiable: 2, findings: [] }, "k", "ts"))).toBe("warning");
  });

  it("the reducer puts the card in its request, and the not-reported notice too", () => {
    const state = reduceEvents(initialThread, [
      ev(1, { type: "turn_started", text: "Verify content", options: { depth: "standard", verify: true } }),
      ev(2, { type: "verification", ...event }),
      ev(3, { type: "notice", level: "warning", message: "Verification was asked for and was not reported." }),
    ]);
    const turn = state.entries.find((e) => "items" in e) as Turn;
    const kinds = turn.items.map((i) => i.type);
    expect(kinds).toContain("verification");
    expect(kinds).toContain("notice");
    const card = turn.items.find((i) => i.type === "verification");
    expect(card && card.type === "verification" && card.failed).toBe(1);
  });
});
