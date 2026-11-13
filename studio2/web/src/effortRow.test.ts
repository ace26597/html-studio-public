import { describe, expect, it } from "vitest";
import { effortSummary } from "./effortRow";

describe("compact depth row summary", () => {
  it("names the depth and the check switch", () => {
    expect(effortSummary({ depth: "deep", verify: true })).toBe("Deep · Check on");
    expect(effortSummary({ depth: "quick", verify: false })).toBe("Quick · Check off");
    expect(effortSummary({ depth: "standard", verify: false })).toBe("Standard · Check off");
  });

  it("reads an unknown depth as Standard", () => {
    expect(effortSummary({ depth: "huge" as never, verify: true })).toBe("Standard · Check on");
  });
});
