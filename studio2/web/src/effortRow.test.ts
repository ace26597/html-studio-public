import { describe, expect, it } from "vitest";
import { effortLabel, effortSummary } from "./effortRow";

describe("the composer's depth button", () => {
  it("shows the depth's name", () => {
    expect(effortLabel({ depth: "deep", verify: true })).toBe("Deep");
    expect(effortLabel({ depth: "quick", verify: false })).toBe("Quick");
  });

  it("says the depth and the check to a screen reader", () => {
    expect(effortSummary({ depth: "deep", verify: true })).toBe("How much to do: Deep, and the result is checked");
    expect(effortSummary({ depth: "standard", verify: false })).toBe("How much to do: Standard");
  });

  it("reads an unknown depth as Standard", () => {
    expect(effortLabel({ depth: "huge" as never, verify: true })).toBe("Standard");
  });
});
