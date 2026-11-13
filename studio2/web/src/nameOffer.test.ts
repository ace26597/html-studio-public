import { describe, expect, it } from "vitest";
import { offeredName } from "./nameOffer";

describe("offeredName", () => {
  it("offers the studio's suggestion only while the page is still called Untitled", () => {
    expect(offeredName({ name: "Untitled", name_suggestion: "Tides — Field Guide" })).toBe("Tides — Field Guide");
    expect(offeredName({ name: "My page", name_suggestion: "Tides — Field Guide" })).toBeNull();
  });

  it("offers nothing when there is no suggestion, it was dismissed, or it says nothing", () => {
    expect(offeredName(null)).toBeNull();
    expect(offeredName({ name: "Untitled" })).toBeNull();
    expect(offeredName({ name: "Untitled", name_suggestion: null })).toBeNull();
    expect(offeredName({ name: "Untitled", name_suggestion: "  " })).toBeNull();
    expect(offeredName({ name: "Untitled", name_suggestion: "Untitled" })).toBeNull();
  });
});
