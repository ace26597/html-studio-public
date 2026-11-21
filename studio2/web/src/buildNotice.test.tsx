import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BuildBanner } from "./BuildBanner";
import { buildLine, buildNotice, type Build } from "./buildNotice";

const fresh: Build = { commit: "4362be2d", committed_at: "2026-11-20T21:51:53-05:00", started_at: "2026-11-21T15:30:18Z", stale: false, newer: null };
const old: Build = { commit: "429b7c09", committed_at: "2026-11-18T17:40:00-05:00", started_at: "2026-11-18T17:20:14Z", stale: true, newer: "4362be2d" };

describe("a server older than its interface says so", () => {
  it("says nothing when they match", () => {
    expect(buildNotice({ build: fresh })).toBeNull();
    expect(buildNotice(null)).toBeNull();
  });

  it("says since when it has run, and who restarts it", () => {
    const mine = buildNotice({ build: old, shared: false })!;
    expect(mine.text).toBe("This studio’s server has been running since 18 November, and its code has changed since.");
    expect(mine.detail).toBe("Newer features stay hidden until it is restarted. Restart the studio to get them.");
    expect(buildNotice({ build: old, shared: true })!.detail).toContain("Its owner restarts it.");
  });

  it("treats a server that reports nothing as older: it was started before servers said", () => {
    const notice = buildNotice({ shared: true })!;
    expect(notice.text).toBe("This studio’s server is older than what you are looking at.");
    expect(notice.key).toBe("no-build");
  });

  it("comes back after it was put away when the server restarts or newer code lands", () => {
    const first = buildNotice({ build: old })!.key;
    expect(buildNotice({ build: { ...old, newer: "abcdef01" } })!.key).not.toBe(first);
    expect(buildNotice({ build: { ...old, started_at: "2026-11-21T16:00:00Z" } })!.key).not.toBe(first);
  });

  it("draws one notice with a way to put it away, and nothing when there is nothing to say", () => {
    const html = renderToStaticMarkup(<BuildBanner health={{ build: old, shared: true }} />);
    expect(html).toContain('role="status"');
    expect(html).toContain("running since 18 November");
    expect(html).toContain('aria-label="Put this notice away"');
    expect(renderToStaticMarkup(<BuildBanner health={{ build: fresh }} />)).toBe("");
  });

  it("names the code at the foot of the Home page", () => {
    expect(buildLine(fresh)).toBe("Server running code 4362be2d since 21 November.");
    expect(buildLine(old)).toBe("Server running code 429b7c09 since 18 November, older than the code on disk.");
    expect(buildLine({ ...fresh, commit: null })).toBe("Server running its own copy of the code since 21 November.");
    expect(buildLine(null)).toBe("");
  });
});
