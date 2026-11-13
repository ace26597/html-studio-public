import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { exampleFacts, modelName, type Example } from "./examples";
import { ExamplesSection } from "./ExamplesSection";

const base: Example = {
  id: "ev-or-petrol-car",
  title: "Electric or petrol car: five-year cost",
  summary: "A deep, sourced comparison.",
  kind: "report",
  made: { model: "opus", date: "2026-11-13", requests: 2, duration_s: 1888, depth: "deep", request: "How much…" },
  sources: 115,
  check: { checked: 381, passed: 372, fixed: 9, failed: 0, unverifiable: 0 },
  files: 9,
  bytes: 350000,
  preview: "/api/examples/ev-or-petrol-car/preview",
};
const bare: Example = {
  ...base,
  id: "tides",
  title: "Tides",
  kind: "explainer",
  made: { model: "openrouter/openai/gpt-6.1-sol", date: null, requests: 1, duration_s: 61, depth: null, request: null },
  sources: 0,
  check: null,
  preview: null,
};

describe("examples that come with the studio", () => {
  it("names the model plainly", () => {
    expect(modelName("opus")).toBe("Opus");
    expect(modelName("openrouter/openai/gpt-6.1-sol")).toBe("gpt-6.1-sol");
    expect(modelName(null)).toBeNull();
  });

  it("says only the facts the record holds", () => {
    expect(exampleFacts(base)).toBe("Opus · Deep · 31 min · 2 requests · 115 sources · 381 figures checked");
    expect(exampleFacts(bare)).toBe("gpt-6.1-sol · 61 s");
    const unknown = { ...bare, made: { ...bare.made, model: null, duration_s: null } };
    expect(exampleFacts(unknown)).toBe("");
  });

  it("draws a card per example with one button each, and nothing when there are none", () => {
    const html = renderToStaticMarkup(<ExamplesSection examples={[base, bare]} opening={null} onOpen={() => {}} />);
    expect(html).toContain("Examples");
    expect(html).toContain("Electric or petrol car: five-year cost");
    expect(html.match(/Open a copy/g)?.length).toBe(2);
    expect(html.match(/<img /g)?.length).toBe(1);             // no picture: no broken image
    expect(html).toContain('src="/api/examples/ev-or-petrol-car/preview"');
    expect(html).toContain("Report");
    expect(html).toContain("Explainer");
    expect(renderToStaticMarkup(<ExamplesSection examples={[]} opening={null} onOpen={() => {}} />)).toBe("");
    const busy = renderToStaticMarkup(<ExamplesSection examples={[base, bare]} opening="tides" onOpen={() => {}} />);
    expect(busy.match(/disabled=""/g)?.length).toBe(2);       // one copy at a time
  });
});
