// The guide and the benchmarks in the projects page's top bar (CONTRACT.md, "The guide and the
// benchmarks, inside the studio"): shown only when this studio serves them.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { GuideLinks } from "./Home";

describe("the guide links", () => {
  it("are two ordinary links to the pages the studio serves", () => {
    const html = renderToStaticMarkup(<GuideLinks site />);
    expect(html).toMatch(/<a class="home-nav-link" href="\/site\/" aria-label="How it works">/);
    expect(html).toMatch(/<a class="home-nav-link" href="\/site\/benchmarks\/" aria-label="Benchmarks">/);
    expect(html).toContain("How it works</span>");
    expect(html).toContain("Benchmarks</span>");
  });

  it("are not there on a studio that has no guide to serve", () => {
    expect(renderToStaticMarkup(<GuideLinks site={false} />)).toBe("");
    expect(renderToStaticMarkup(<GuideLinks />)).toBe("");
  });
});
