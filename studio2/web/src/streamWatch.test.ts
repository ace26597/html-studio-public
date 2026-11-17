import { describe, expect, it } from "vitest";
import { STALL_MS, streamStalled } from "./streamWatch";

describe("a stalled event stream", () => {
  it("is three missed keep-alives of silence", () => {
    expect(STALL_MS).toBe(45_000);
  });

  it("counts as stalled only after that long with nothing at all", () => {
    expect(streamStalled(1_000, 1_000 + STALL_MS - 1)).toBe(false);
    expect(streamStalled(1_000, 1_000 + STALL_MS)).toBe(true);
    // A clock that went backwards (or a sleep that the timer did not see) is not a stall by itself.
    expect(streamStalled(10_000, 5_000)).toBe(false);
  });
});
