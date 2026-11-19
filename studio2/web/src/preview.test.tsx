// A page with its own name that never says `ready` gets a notice over the frame (CONTRACT.md,
// "A page with its own name that shows nothing in the studio"); a page on the studio's host never does.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { hasOwnName, SILENT_AFTER_MS, SilentNotice } from "./Preview";

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
