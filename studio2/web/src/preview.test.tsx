// A page with its own name that never says `ready` gets a notice over the frame (CONTRACT.md,
// "A page with its own name that shows nothing in the studio"); a page on the studio's host never does.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  HAND_OFF_AGAIN_MS,
  HAND_OFF_WINDOW_MS,
  handOffUrl,
  hasOwnName,
  hereUrl,
  mayHandOff,
  SILENT_AFTER_MS,
  SilentNotice,
} from "./Preview";

describe("hasOwnName", () => {
  it("is true only for another host than the studio's, whatever the port", () => {
    expect(hasOwnName("https://shop-studio.example.com/", "studio.example.com")).toBe(true);
    expect(hasOwnName("http://127.0.0.1:8203/", "127.0.0.1")).toBe(false);
    expect(hasOwnName("https://mac.tailnet.ts.net:8203/", "mac.tailnet.ts.net")).toBe(false);
  });

  it("is false for something that is not an address", () => {
    expect(hasOwnName("not an address", "studio.example.com")).toBe(false);
  });
});

describe("the notice over a page that shows nothing", () => {
  const html = renderToStaticMarkup(
    <SilentNotice previewUrl="https://shop-studio.example.com/" onTryAgain={() => {}} />,
  );

  it("names the page's own address and says what to do", () => {
    expect(html).toContain("This page has not loaded here");
    expect(html).toContain("shop-studio.example.com");
    expect(html).toContain("Open the page once in its own tab, then come back.");
    expect(html).toContain('role="status"');
  });

  it("offers the page in a new tab and a way to try again", () => {
    expect(html).toMatch(/<a [^>]*href="https:\/\/shop-studio\.example\.com\/"[^>]*target="_blank"[^>]*>Open the page<\/a>/);
    expect(html).toMatch(/<button type="button"[^>]*>Try again<\/button>/);
  });

  it("waits long enough for a slow first load", () => {
    expect(SILENT_AFTER_MS).toBeGreaterThanOrEqual(8000);
  });
});

// CONTRACT.md, "A page with its own address signs in by itself".
describe("the trip to a page's own address and back", () => {
  it("asks the page's address for the picture, never a kept copy", () => {
    expect(hereUrl("https://shop-studio.example.com", 1700000000000)).toBe(
      "https://shop-studio.example.com/__studio/here.gif?1700000000000",
    );
  });

  it("goes to the page's address and names where to come back to", () => {
    expect(handOffUrl("https://shop-studio.example.com", "https://studio.example.com/p/p_1?x=1&y=2")).toBe(
      "https://shop-studio.example.com/__studio/back?to=https%3A%2F%2Fstudio.example.com%2Fp%2Fp_1%3Fx%3D1%26y%3D2",
    );
  });

  it("is made by itself only just after the project opened", () => {
    expect(mayHandOff(null, 1000, 1000 + HAND_OFF_WINDOW_MS)).toBe(true);
    expect(mayHandOff(null, 1000, 1001 + HAND_OFF_WINDOW_MS)).toBe(false);
  });

  it("is not made twice in a row from one tab", () => {
    const opened = 10_000_000;
    expect(mayHandOff(opened - 5000, opened, opened + 100)).toBe(false);
    expect(mayHandOff(opened - HAND_OFF_AGAIN_MS - 1, opened, opened + 100)).toBe(true);
    expect(mayHandOff(Number.NaN, opened, opened + 100)).toBe(true);
  });

  const html = renderToStaticMarkup(
    <SilentNotice
      previewUrl="https://shop-studio.example.com/"
      onTryAgain={() => {}}
      backTo="https://studio.example.com/p/p_1"
    />,
  );

  it("the notice then offers one tap in this tab instead of a second tab", () => {
    expect(html).toContain("This page needs you signed in at its own address");
    expect(html).toContain("shop-studio.example.com");
    expect(html).toMatch(
      /<a [^>]*href="https:\/\/shop-studio\.example\.com\/__studio\/back\?to=https%3A%2F%2Fstudio\.example\.com%2Fp%2Fp_1"[^>]*>Sign in to this page<\/a>/,
    );
    expect(html).not.toContain('target="_blank"');
    expect(html).toMatch(/<button type="button"[^>]*>Try again<\/button>/);
  });
});
