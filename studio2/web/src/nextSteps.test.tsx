import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CHECK_ONLY_OPTIONS, POLISH_OPTIONS, POLISH_TEXT, VERIFY_ONLY_TEXT } from "./effort";
import { fillDraft, optionsForText, shownSteps, stepsFrom, type NextStep } from "./nextSteps";
import { initialThread, lastTurn, reduceEvents, type Turn } from "./thread";
import { NextStepChips } from "./ThreadView";
import type { StudioEvent } from "./types";

const ev = (seq: number, e: Record<string, unknown>, turn = "t_a") =>
  ({ seq, ts: `2026-11-13T09:00:0${seq}Z`, turn_id: turn, ...e }) as unknown as StudioEvent;

const check = { id: "check", from: "studio", label: "Verify content", text: VERIFY_ONLY_TEXT, options: CHECK_ONLY_OPTIONS };
const look = { id: "look", from: "studio", label: "Polish the look", text: POLISH_TEXT, options: POLISH_OPTIONS };
const add = { id: "a1", from: "agent", label: "Add a section on battery life", text: "Add a section on battery life." };
const finished = { type: "turn_finished", status: "completed", error: null, usage: null, session_id: "s" };

describe("suggested next steps", () => {
  it("reads the event, dropping what is malformed", () => {
    const steps = stepsFrom({
      steps: [check, add, { id: "a2", label: "", text: "x" }, { id: "a3", label: "No text" }, "nope", null,
        { id: "a4", from: "someone", label: "Odd options", text: "Do it.", options: { depth: "huge", verify: "yes" } }],
    });
    expect(steps.map((s) => s.id)).toEqual(["check", "a1", "a4"]);
    expect(steps[0].options).toEqual(CHECK_ONLY_OPTIONS);
    expect(steps[1].options).toBeUndefined();
    expect(steps[2].from).toBe("agent");                    // anything but "studio" is the agent's
    expect(steps[2].options).toBeUndefined();               // options that make no sense are not sent
    expect(stepsFrom({ steps: "Add a chart" })).toEqual([]);
    expect(stepsFrom({})).toEqual([]);
  });

  it("belongs to its request and is shown under the last finished one only", () => {
    const first = [
      ev(1, { type: "turn_started", text: "Make a report" }),
      ev(2, { type: "next_steps", steps: [check, add] }),
    ];
    let state = reduceEvents(initialThread, first);
    let turn = lastTurn(state) as Turn;
    expect(turn.nextSteps?.map((s) => s.label)).toEqual(["Verify content", "Add a section on battery life"]);
    expect(shownSteps(turn, true)).toEqual([]);             // still running
    state = reduceEvents(state, [ev(3, finished)]);
    turn = lastTurn(state) as Turn;
    expect(shownSteps(turn, true).map((s) => s.id)).toEqual(["check", "a1"]);
    expect(shownSteps(turn, false)).toEqual([]);            // an older request's chips are not shown again
    expect(shownSteps(turn, true, true)).toEqual([]);       // something is queued or starting
    const failed = { ...turn, status: "failed" as const };
    expect(shownSteps(failed, true)).toEqual([]);
    // A next request takes the place of the last one.
    state = reduceEvents(state, [ev(4, { type: "turn_started", text: "Add a section" }, "t_b")]);
    expect((lastTurn(state) as Turn).id).toBe("t_b");
    expect((lastTurn(state) as Turn).nextSteps ?? []).toEqual([]);
  });

  it("a tap fills the box and never throws away what is typed", () => {
    expect(fillDraft("", add.text)).toBe(add.text);
    expect(fillDraft("   \n", add.text)).toBe(add.text);
    expect(fillDraft("Also make it shorter.", add.text)).toBe(`Also make it shorter.\n${add.text}`);
    expect(fillDraft("Also make it shorter.  \n\n", add.text)).toBe(`Also make it shorter.\n${add.text}`);
    expect(fillDraft(add.text, add.text)).toBe(add.text);                    // a second tap adds nothing
    expect(fillDraft(`Typed.\n${add.text}`, add.text)).toBe(`Typed.\n${add.text}`);
  });

  it("sends a step's options only with its exact text", () => {
    const steps = [check, add] as NextStep[];
    expect(optionsForText(steps, VERIFY_ONLY_TEXT)).toEqual(CHECK_ONLY_OPTIONS);
    expect(optionsForText(steps, `  ${VERIFY_ONLY_TEXT}\n`)).toEqual(CHECK_ONLY_OPTIONS);
    expect(optionsForText(steps, `${VERIFY_ONLY_TEXT} And the charts.`)).toBeUndefined();   // edited: an ordinary request
    expect(optionsForText(steps, add.text)).toBeUndefined();
    expect(optionsForText([], VERIFY_ONLY_TEXT)).toBeUndefined();
    // "Polish the look" from a chip is the same polish request as the menu's.
    const both = stepsFrom({ steps: [check, look, add] });
    expect(both.map((s) => s.id)).toEqual(["check", "look", "a1"]);
    expect(both[1].options).toEqual(POLISH_OPTIONS);
    expect(optionsForText(both, POLISH_TEXT)).toEqual({ depth: "standard", verify: false, polish: true });
    expect(optionsForText(both, VERIFY_ONLY_TEXT)).toEqual(CHECK_ONLY_OPTIONS);
  });

  it("draws one button per step, saying that a tap only fills the box", () => {
    const html = renderToStaticMarkup(<NextStepChips steps={[check, add] as NextStep[]} onUse={() => {}} />);
    expect(html.match(/<button/g)?.length).toBe(2);
    expect(html).toContain("Verify content");
    expect(html).toContain("Add a section on battery life");
    expect(html).toMatch(/type="button"/);
    expect(html).toMatch(/aria-label="Suggested next steps"/);
    expect(html).toContain(`title="Puts this in the chat box: ${add.text}"`);
    expect(renderToStaticMarkup(<NextStepChips steps={[]} onUse={() => {}} />)).toBe("");
  });
});
