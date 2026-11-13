import { describe, expect, it } from "vitest";
import { emptyLogBuffer, mergeLogs } from "./appLogs";

const lines = (from: number, to: number) =>
  Array.from({ length: to - from + 1 }, (_, i) => ({ n: from + i, text: `line ${from + i}` }));

describe("mergeLogs", () => {
  it("appends new lines and moves the cursor", () => {
    const first = mergeLogs(emptyLogBuffer, { lines: lines(1, 3), last: 3 }, 0);
    expect(first.restarted).toBe(false);
    expect(first.buffer.after).toBe(3);
    const second = mergeLogs(first.buffer, { lines: lines(4, 5), last: 5 }, 3);
    expect(second.buffer.lines.map((line) => line.n)).toEqual([1, 2, 3, 4, 5]);
    expect(second.buffer.after).toBe(5);
  });

  it("returns the same buffer when nothing is new", () => {
    const { buffer } = mergeLogs(emptyLogBuffer, { lines: lines(1, 2), last: 2 }, 0);
    const again = mergeLogs(buffer, { lines: [], last: 2 }, 2);
    expect(again.buffer).toBe(buffer);
  });

  it("detects a restart when last is smaller than after, and empties the buffer", () => {
    const { buffer } = mergeLogs(emptyLogBuffer, { lines: lines(1, 40), last: 40 }, 0);
    const restarted = mergeLogs(buffer, { lines: [], last: 3 }, 40);
    expect(restarted.restarted).toBe(true);
    expect(restarted.buffer).toEqual(emptyLogBuffer);
  });

  it("keeps reading a capped answer from where it stopped", () => {
    const first = mergeLogs(emptyLogBuffer, { lines: lines(1, 2000), last: 2500 }, 0);
    expect(first.buffer.after).toBe(2000);
    const second = mergeLogs(first.buffer, { lines: lines(2001, 2500), last: 2500 }, 2000);
    expect(second.buffer.after).toBe(2500);
  });

  it("skips past lines the server no longer holds", () => {
    const { buffer } = mergeLogs(emptyLogBuffer, { lines: [], last: 9000 }, 0);
    expect(buffer.after).toBe(9000);
  });

  it("drops lines it already has from a late answer", () => {
    const { buffer } = mergeLogs(emptyLogBuffer, { lines: lines(1, 5), last: 5 }, 0);
    const late = mergeLogs(buffer, { lines: lines(3, 6), last: 6 }, 2);
    expect(late.buffer.lines.map((line) => line.n)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("caps the buffer and says so", () => {
    const { buffer } = mergeLogs(emptyLogBuffer, { lines: lines(1, 12), last: 12 }, 0, 10);
    expect(buffer.lines).toHaveLength(10);
    expect(buffer.lines[0].n).toBe(3);
    expect(buffer.trimmed).toBe(true);
  });
});
