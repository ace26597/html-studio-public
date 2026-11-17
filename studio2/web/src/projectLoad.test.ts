import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { refreshFailure } from "./projectLoad";

const offline = new ApiError(0, "network_error", "Could not reach the studio server.");
const gone = new ApiError(404, "not_found", "No such project.");

describe("a failed read of the project record", () => {
  it("shows the error screen on the first load", () => {
    expect(refreshFailure(false, offline)).toEqual({ missing: false, message: "Could not reach the studio server." });
    expect(refreshFailure(false, gone)).toEqual({ missing: true, message: "No such project." });
  });

  it("keeps the page when a later refresh fails", () => {
    expect(refreshFailure(true, offline)).toBeNull();
    expect(refreshFailure(true, new ApiError(500, "http_500", "Boom"))).toBeNull();
    expect(refreshFailure(true, new Error("Failed to fetch"))).toBeNull();
  });

  it("still says so when the project was deleted while it was open", () => {
    expect(refreshFailure(true, gone)).toEqual({ missing: true, message: "No such project." });
  });
});
