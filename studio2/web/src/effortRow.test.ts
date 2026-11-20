import { describe, expect, it } from "vitest";
import { VERIFY_OFF_TIP, VERIFY_ON_TIP, effortLabel } from "./effortRow";

describe("the settings strip under the composer", () => {
  it("shows the depth's name", () => {
    expect(effortLabel({ depth: "deep", verify: true })).toBe("Deep");
    expect(effortLabel({ depth: "quick", verify: false })).toBe("Quick");
  });

  it("reads an unknown depth as Standard", () => {
    expect(effortLabel({ depth: "huge" as never, verify: true })).toBe("Standard");
  });

  it("says what the Verify switch does in both states", () => {
    expect(VERIFY_OFF_TIP.startsWith("Off.")).toBe(true);
    expect(VERIFY_ON_TIP.startsWith("On:")).toBe(true);
  });
});
