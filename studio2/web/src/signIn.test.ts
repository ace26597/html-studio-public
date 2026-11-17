import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, request, whenAccessDenied } from "./api";
import {
  goToSignIn,
  healthFailure,
  holdSignIn,
  needsSignIn,
  onSignInChange,
  resetSignInForTests,
  signedInAgain,
  signInWaiting,
  signInUrl,
} from "./signIn";

describe("a 401 from any call in the middle of a session", () => {
  const answer = (status: number, code: string) =>
    new Response(JSON.stringify({ error: { code, message: "Sign in to this studio first." } }), { status });

  afterEach(() => {
    vi.unstubAllGlobals();
    whenAccessDenied(null);
    resetSignInForTests();
  });

  it("goes to sign in from any API call answered 401 access_denied", async () => {
    const denied = vi.fn();
    whenAccessDenied(denied);
    vi.stubGlobal("fetch", vi.fn(async () => answer(401, "access_denied")));
    await expect(request("GET", "/projects")).rejects.toMatchObject({ status: 401, code: "access_denied" });
    await expect(request("POST", "/projects/p_1/turns", { text: "x" })).rejects.toBeInstanceOf(ApiError);
    expect(denied).toHaveBeenCalledTimes(2);
  });

  it("does not for another refusal (a gateway key refused, the machine-only studio)", async () => {
    const denied = vi.fn();
    whenAccessDenied(denied);
    vi.stubGlobal("fetch", vi.fn(async () => answer(401, "bad_token")));
    await expect(request("GET", "/x")).rejects.toBeInstanceOf(ApiError);
    vi.stubGlobal("fetch", vi.fn(async () => answer(403, "access_denied")));
    await expect(request("GET", "/x")).rejects.toBeInstanceOf(ApiError);
    expect(denied).not.toHaveBeenCalled();
  });

  it("sends the browser once, keeping the address, and never from the sign-in page", () => {
    const assign = vi.fn();
    const location = { pathname: "/p/p_1", href: "http://studio.example:8200/p/p_1", assign };
    vi.stubGlobal("window", { location });
    goToSignIn();
    goToSignIn();
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith("/login?next=http%3A%2F%2Fstudio.example%3A8200%2Fp%2Fp_1");
    resetSignInForTests();
    location.pathname = "/login";
    goToSignIn();
    expect(assign).toHaveBeenCalledTimes(1);
  });
});

describe("a 401 while the file viewer holds unsaved edits", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    resetSignInForTests();
  });

  it("stays on the page and says so, then works again once signed in, with no reload", () => {
    const assign = vi.fn();
    const location = { pathname: "/p/p_1", href: "http://studio.example:8200/p/p_1", assign };
    vi.stubGlobal("window", { location });
    let dirty = true;
    const release = holdSignIn(() => dirty);
    const seen: boolean[] = [];
    const off = onSignInChange(() => seen.push(signInWaiting()));
    goToSignIn();
    goToSignIn();
    expect(assign).not.toHaveBeenCalled();
    expect(signInWaiting()).toBe(true);
    expect(seen).toEqual([true]);
    // Signed in in another tab: a check is answered.
    signedInAgain();
    expect(signInWaiting()).toBe(false);
    expect(seen).toEqual([true, false]);
    // A later 401 with the edits still there waits again: nothing is stuck.
    goToSignIn();
    expect(signInWaiting()).toBe(true);
    // Once the edits are saved or dropped, the next 401 goes to the sign-in page, once.
    dirty = false;
    goToSignIn();
    goToSignIn();
    expect(assign).toHaveBeenCalledTimes(1);
    off();
    release();
  });

  it("a guard released by its page no longer holds", () => {
    const assign = vi.fn();
    vi.stubGlobal("window", { location: { pathname: "/", href: "http://studio.example:8200/", assign } });
    const release = holdSignIn(() => true);
    release();
    goToSignIn();
    expect(assign).toHaveBeenCalledTimes(1);
  });
});

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
