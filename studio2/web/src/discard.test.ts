import { describe, expect, it } from "vitest";
import { DISCARD_QUESTION, latestOnly, mayDiscard } from "./discard";

describe("closing a file with unsaved edits", () => {
  it("asks only when there is something to lose", () => {
    const asked: string[] = [];
    const ask = (answer: boolean) => (question: string) => {
      asked.push(question);
      return answer;
    };
    expect(mayDiscard(false, ask(false))).toBe(true);
    expect(asked).toEqual([]);
    expect(mayDiscard(true, ask(false))).toBe(false);
    expect(mayDiscard(true, ask(true))).toBe(true);
    expect(asked).toEqual([DISCARD_QUESTION, DISCARD_QUESTION]);
  });
});

describe("answers that arrive out of order", () => {
  it("only the newest request may show its answer", () => {
    const loads = latestOnly();
    const a = loads.begin();
    const b = loads.begin();
    expect(loads.isLatest(a)).toBe(false);
    expect(loads.isLatest(b)).toBe(true);
    // Closing the viewer starts nothing new but still retires what is in flight.
    loads.begin();
    expect(loads.isLatest(b)).toBe(false);
  });
});
