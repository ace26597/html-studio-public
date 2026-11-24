import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LibraryView } from "./LibraryView";
import type { Library } from "./types";

// Shapes for the rules under test only; not product data.
const library: Library = {
  connections: [{ id: "a", label: "Conn A", kind: "fake", available: true, reason: null, models: [], default_model: null }],
  tools: [{ id: "t1", label: "Tool one", description: "Does one", available: true, note: null }],
  studio_tools: [{ name: "fetch_source", description: "Saves a source", available: true, note: null }],
  agents: [{ name: "researcher", description: "Finds sources" }],
  skills: [
    { id: "studio:s1", name: "s1", label: "Studio skill", description: "From the studio", source: "studio", default: true },
    { id: "built-in:b", label: "Built skill", description: "Built in", source: "built-in" },
  ],
  mcp_servers: [
    {
      id: "m1",
      name: "machine-one",
      label: null,
      transport: "http",
      url: "https://example.test/m1",
      command: null,
      args: [],
      secret_names: [],
      source: "machine",
      description: null,
      last_test: null,
    },
  ],
  templates: [{ id: "x", label: "Tpl", description: "d", app: false }],
  data_kinds: [],
  observed_at: null,
};

const state = { library, error: null, reload: () => {} };
const draw = (props: Partial<Parameters<typeof LibraryView>[0]> = {}) =>
  renderToStaticMarkup(<LibraryView state={state} compact manage={false} {...props} />);

afterEach(() => vi.unstubAllGlobals());

describe("the Library opens on Skills, Connectors and Templates", () => {
  it("puts the three first, in order, with the search above them and Everything else closed at the end", () => {
    const html = draw();
    const at = (text: string) => html.indexOf(text);
    expect(at("Search the library")).toBeGreaterThan(-1);
    expect(at("Search the library")).toBeLessThan(at(">Skills"));
    expect(at(">Skills")).toBeLessThan(at(">Connectors"));
    expect(at(">Connectors")).toBeLessThan(at(">Templates"));
    expect(at(">Templates")).toBeLessThan(at("lib-rest-toggle"));
    expect(html).toContain('aria-expanded="false"');
    // What is under Everything else is not drawn while it is closed.
    expect(html).not.toContain("Tool one");
    expect(html).not.toContain("researcher");
    expect(html).not.toContain("machine-one");
    // The front says how many connectors are found on this computer.
    expect(html).toContain("Another 1 connector found on this computer is under");
  });

  it("opens Everything else for a link to something in it, and when the browser remembers it open", () => {
    const linked = draw({ openMore: true });
    expect(linked).toContain("Tool one");
    expect(linked).toContain("researcher");
    expect(linked).toContain("machine-one");
    expect(linked).toContain("Built skill");

    vi.stubGlobal("localStorage", { getItem: (key: string) => (key === "studio2:library.more" ? "open" : null) });
    const remembered = draw();
    expect(remembered).toContain('aria-expanded="true"');
    expect(remembered).toContain("Tool one");
  });
});
