import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { healthFailure, needsSignIn, signInUrl } from "./signIn";

describe("a health check that fails", () => {
  it("sends a browser whose access key expired or changed to sign in", () => {
    const denied = new ApiError(401, "access_denied", "Sign in to this studio first.");
    expect(healthFailure(denied)).toEqual({ kind: "sign-in" });
    expect(needsSignIn(denied)).toBe(true);
  });

  it("calls the studio down only when nothing answered for it", () => {
    expect(healthFailure(new ApiError(0, "network_error", "Could not reach the studio server."))).toEqual({ kind: "down" });
    // A proxy in front (Vite in development, a tunnel) answers for a studio that is not there.
    for (const status of [502, 503, 504]) expect(healthFailure(new ApiError(status, `http_${status}`, "x"))).toEqual({ kind: "down" });
    expect(healthFailure(new TypeError("Failed to fetch"))).toEqual({ kind: "down" });
  });

  it("shows any other answer as what it is, not as a studio that is not running", () => {
    const local = new ApiError(403, "access_denied", "This studio only answers on the machine it runs on.");
    expect(healthFailure(local)).toEqual({ kind: "error", message: "This studio only answers on the machine it runs on." });
    expect(healthFailure(new ApiError(500, "http_500", "Boom"))).toEqual({ kind: "error", message: "Boom" });
    expect(needsSignIn(local)).toBe(false);
    expect(needsSignIn(new Error("x"))).toBe(false);
  });
});

describe("the sign-in address", () => {
  it("brings the person back to where they were", () => {
    expect(signInUrl("http://100.64.1.2:8200/p/p_1?x=1#a")).toBe(
      "/login?next=http%3A%2F%2F100.64.1.2%3A8200%2Fp%2Fp_1%3Fx%3D1%23a",
    );
  });
});
