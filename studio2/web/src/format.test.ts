import { describe, expect, it } from "vitest";
import { appStateLabel, baseName, costLabel, ESTIMATE_TIP, isImagePath, PENDING_TIP, RECOVERED_TIP, UNPRICED_TIP,
  backupTip,
} from "./format";

describe("costLabel", () => {
  it("shows nothing when the cost is unknown (gateway)", () => {
    expect(costLabel({ cost_usd: null, cost_estimated: false })).toBeNull();
    expect(costLabel({ cost_usd: null })).toBeNull();
    expect(costLabel(null)).toBeNull();
  });

  it("never shows a bare dollar figure for an estimate", () => {
    const label = costLabel({ cost_usd: 1.0234, cost_estimated: true });
    expect(label).toEqual({ text: "≈ $1.02 at API prices", tip: ESTIMATE_TIP });
    expect(ESTIMATE_TIP).toBe("Not a charge: this ran on your Claude subscription");
  });

  it("words a tiny estimate without a misleading ≈", () => {
    expect(costLabel({ cost_usd: 0.004, cost_estimated: true })?.text).toBe("under $0.01 at API prices");
  });

  it("shows a real charge as a plain amount", () => {
    expect(costLabel({ cost_usd: 0.84, cost_estimated: false })).toEqual({ text: "$0.84", tip: null });
  });

  it("shows nothing when the flag is missing (cannot tell an estimate from a charge)", () => {
    expect(costLabel({ cost_usd: 0.84 })).toBeNull();
  });

  it("says a gateway turn's price is pending rather than showing nothing or zero", () => {
    expect(costLabel({ cost_usd: null, cost_estimated: false, cost_pending: true })).toEqual({
      text: "cost pending",
      tip: PENDING_TIP,
    });
  });

  it("counts requests nobody priced instead of calling them free", () => {
    expect(costLabel({ cost_usd: null, cost_estimated: false, unpriced_requests: 3 })).toEqual({
      text: "3 requests unpriced",
      tip: UNPRICED_TIP,
    });
    expect(costLabel({ cost_usd: null, unpriced_requests: 1 })?.text).toBe("1 request unpriced");
    expect(costLabel({ cost_usd: null, cost_estimated: false, unpriced_requests: 0 })).toBeNull();
    expect(costLabel({ cost_usd: 0.84, cost_estimated: false, unpriced_requests: 2 })).toEqual({
      text: "$0.84 + 2 unpriced",
      tip: UNPRICED_TIP,
    });
  });

  it("says when the studio worked a figure out itself, and from whose rates", () => {
    const some = costLabel({
      cost_usd: 0.27, cost_estimated: false, cost_basis: "backup",
      backup: { requests: 2, usd: 0.24, rates: "list", as_of: "2026-11-13" },
    });
    expect(some?.text).toBe("$0.27 calculated by the studio");
    expect(some?.tip).toContain("2 requests came back with no cost");
    expect(some?.tip).toContain("the studio's price list of 2026-11-13");
    expect(some?.tip).toContain("not a bill");
    const whole = costLabel({
      cost_usd: 0.02, cost_estimated: false, cost_basis: "backup", unpriced_requests: 1,
      backup: { requests: null, usd: 0.02, rates: "gateway", as_of: null },
    });
    expect(whole?.text).toBe("$0.02 calculated by the studio + 1 unpriced");
    expect(whole?.tip).toContain("spend log could not be read");
    expect(whole?.tip).toContain("the gateway's own price for the model");
    expect(whole?.tip).toContain("each model at its own rate");
    expect(whole?.tip).toContain("List prices, not a bill");
    // a request that was cut short, and a helper model nobody prices: both are said
    const cut = backupTip({ requests: null, rates: "list", as_of: "2026-11-19", unpriced_models: ["a-model"], partial: true });
    expect(cut).toContain("Not in this figure, for want of a rate: a-model.");
    expect(cut).toContain("it cost at least this");
    expect(
      costLabel({ cost_usd: 0.4, cost_estimated: false, cost_basis: "backup", backup: { requests: null, usd: 0.4, rates: "list", as_of: null, partial: true } })?.text,
    ).toBe("at least $0.40 calculated by the studio");
    expect(backupTip({ requests: 1, rates: "both", as_of: "2026-11-13" })).toContain("1 request came back with no cost, so the studio priced it");
  });

  it("says when a charge was calculated from a price list, not reported by the provider", () => {
    const label = costLabel({ cost_usd: 0.84, cost_estimated: false, cost_basis: "calculated" });
    expect(label?.text).toBe("$0.84 calculated");
    expect(label?.tip).toContain("price list");
    expect(costLabel({ cost_usd: 0.84, cost_estimated: false, cost_basis: "reported" })).toEqual({ text: "$0.84", tip: null });
  });

  it("says when a price was found by session and time", () => {
    expect(costLabel({ cost_usd: 4.446269, cost_estimated: false, cost_source: "recovered" })).toEqual({
      text: "$4.45",
      tip: RECOVERED_TIP,
    });
  });

  it("rejects nonsense amounts", () => {
    expect(costLabel({ cost_usd: Number.NaN, cost_estimated: false })).toBeNull();
    expect(costLabel({ cost_usd: -1, cost_estimated: false })).toBeNull();
  });
});

describe("appStateLabel", () => {
  it("names each state", () => {
    expect(appStateLabel({ state: "running", exitCode: null })).toEqual({ label: "Running", tone: "running" });
    expect(appStateLabel({ state: "starting", exitCode: null }).label).toBe("Starting");
    expect(appStateLabel({ state: "stopped", exitCode: null }).label).toBe("Stopped");
    expect(appStateLabel({ state: "none", exitCode: null }).tone).toBe("none");
  });

  it("includes the exit code of a crash when there is one", () => {
    expect(appStateLabel({ state: "crashed", exitCode: 1 }).label).toBe("Crashed · exit 1");
    expect(appStateLabel({ state: "crashed", exitCode: null }).label).toBe("Crashed");
  });
});

describe("paths", () => {
  it("finds base names and images", () => {
    expect(baseName("attachments/photo.PNG")).toBe("photo.PNG");
    expect(isImagePath("attachments/photo.PNG")).toBe(true);
    expect(isImagePath("data.csv")).toBe(false);
  });
});

import { projectFileUrl } from "./format";

describe("projectFileUrl", () => {
  it("loads workspace files from __studio/files on the project URL", () => {
    expect(projectFileUrl("http://127.0.0.1:8201/", "attachments/photo.png")).toBe(
      "http://127.0.0.1:8201/__studio/files/attachments/photo.png",
    );
  });

  it("encodes each segment but keeps the slashes", () => {
    expect(projectFileUrl("http://127.0.0.1:8201/", "my files/a #1?.png")).toBe(
      "http://127.0.0.1:8201/__studio/files/my%20files/a%20%231%3F.png",
    );
  });

  it("copes with a preview URL without a trailing slash and a leading slash in the path", () => {
    expect(projectFileUrl("http://127.0.0.1:8201", "/index.html")).toBe("http://127.0.0.1:8201/__studio/files/index.html");
  });
});

import { checkpointLabel } from "./format";
import { parseRoute } from "./router";

describe("checkpointLabel", () => {
  it("labels uploads by file", () => {
    expect(checkpointLabel("user", "Upload attachments/photo.png")).toBe("Uploaded photo.png");
    expect(checkpointLabel("agent", "Upload data.csv")).toBe("Uploaded data.csv");
  });

  it("leaves everything else as before", () => {
    expect(checkpointLabel("user", "Edit index.html")).toBe("Saved your edit");
    expect(checkpointLabel("agent", "Build the page")).toBe("Saved version");
    expect(checkpointLabel("restore", "Restore x")).toBe("Restored a version");
    expect(checkpointLabel("init", "Blank page")).toBe("Page created");
    expect(checkpointLabel("other", "Uploaded later")).toBe("Saved version");
  });
});

describe("routes", () => {
  it("knows the library, with or without a section anchor", () => {
    expect(parseRoute("/library")).toEqual({ name: "library" });
    expect(parseRoute("/library#data-sources")).toEqual({ name: "library" });
    expect(parseRoute("/p/p_1a2b3c4d#x")).toEqual({ name: "project", id: "p_1a2b3c4d" });
    expect(parseRoute("/")).toEqual({ name: "home" });
  });
});
