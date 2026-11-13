import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ServiceKeyRows } from "./ServiceKeysBlock";
import { canRemove, keyState, type ServiceKey } from "./serviceKeys";

const none: ServiceKey = {
  name: "tavily", label: "Tavily", used_for: "Web search: asked first.", env: "TAVILY_API_KEY",
  site: "https://tavily.com", configured: false, source: null,
};
const saved: ServiceKey = { ...none, configured: true, source: "store" };
const fromEnv: ServiceKey = { ...none, configured: true, source: "env" };

describe("keys for other services", () => {
  it("says where a key is, and removes only one the studio saved", () => {
    expect(keyState(none)).toContain("TAVILY_API_KEY");
    expect(keyState(saved)).toContain("keychain");
    expect(keyState(fromEnv)).toContain("did not save it");
    expect([none, saved, fromEnv].map(canRemove)).toEqual([false, true, false]);
  });

  it("draws a password field, never a key, and Remove only for a saved one", () => {
    const html = renderToStaticMarkup(<ServiceKeyRows services={[saved]} busy={null} onSave={() => {}} onRemove={() => {}} />);
    expect(html).toContain('type="password"');
    expect(html).toContain("Key set");
    expect(html).toContain("Remove");
    expect(html).not.toContain("tvly-");
    const env = renderToStaticMarkup(<ServiceKeyRows services={[fromEnv, none]} busy={null} onSave={() => {}} onRemove={() => {}} />);
    expect(env).not.toContain("Remove");
    expect(env).toContain("No key");
  });
});
