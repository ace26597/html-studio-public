import { describe, expect, it } from "vitest";
import { parseRoute } from "./router";
import {
  chosenModels,
  afterFetch,
  filterListed,
  listNote,
  unknownModels,
  formProblem,
  initialForm,
  isOtherAccount,
  keyMissing,
  keyPlace,
  makeAccessKey,
  needsAccessKey,
  onTailnet,
  parseModels,
  priceLabel,
  toAnswers,
  toggleModel,
  withAccount,
  type SetupAccount,
  type SetupInfo,
} from "./setup";

const account = (name: string, extra: Partial<SetupAccount> = {}): SetupAccount => ({
  account: name,
  kind: name,
  id: name,
  label: name,
  summary: "",
  key: "none",
  fields: [],
  models: ["sonnet", "haiku"],
  default_model: "sonnet",
  available: true,
  note: null,
  ...extra,
});

function info(extra: Partial<SetupInfo> = {}, current: Partial<SetupInfo["current"]> = {}): SetupInfo {
  return {
    needed: true,
    fake: false,
    version: "0.1.0",
    config_path: "~/.config/studio2/config.json",
    config_exists: false,
    secrets: { backend: "keychain", label: "the macOS Keychain" },
    detected: {
      claude_login: { found: true, plan: "max", detail: "Signed in." },
      tailscale: { found: true, address: "100.101.102.103", detail: "On Tailscale." },
    },
    accounts: [
      account("plan", { kind: "claude-subscription", id: "subscription", models: ["opus", "sonnet", "haiku"], default_model: "opus" }),
      account("api-key", { kind: "anthropic-api", id: "api", key: "required" }),
      account("vertex", { kind: "vertex", id: "gcp", fields: ["region", "project"] }),
      account("gateway", { kind: "anthropic-gateway", id: "gateway", key: "required", fields: ["base_url", "small_model"], models: [], default_model: null }),
      account("openai", { kind: "litellm", id: "openai", key: "required", fields: ["base_url", "small_model"], models: [], default_model: null, group: "other", provider: "openai", lists_models: true, needs_litellm: true }),
      account("azure-openai", { kind: "litellm", id: "azure-openai", key: "required", fields: ["base_url", "api_version", "small_model"], models: [], default_model: null, group: "other", provider: "azure-openai", needs_litellm: true }),
    ],
    current: { connections: [], port: 8200, hosts: [], open_tailnet: false, shared: false, access_key_set: false, done: false, ...current },
    running: { port: 8200, hosts: [], open_tailnet: false, shared: false, https: false },
    network_warning: "Anyone who can reach that address can run commands.",
    restart_command: "studio stop && studio start",
    ...extra,
  };
}

describe("setup form", () => {
  it("starts on the plan when a sign-in was found, on a key when not", () => {
    expect(initialForm(info()).account).toBe("plan");
    expect(initialForm(info()).models).toEqual(["opus", "sonnet", "haiku"]);
    const none = info({ detected: { claude_login: { found: false, plan: null, detail: "" }, tailscale: { found: false, address: null, detail: "" } } });
    expect(initialForm(none).account).toBe("api-key");
    expect(initialForm(none).host).toBe("");
  });

  it("keeps what is configured until an account is chosen", () => {
    const set = info({ config_exists: true }, {
      connections: [{ id: "api", kind: "anthropic-api", label: "Anthropic API key", account: "api-key", models: ["haiku"], default_model: "haiku", key_from: "keychain" }],
      port: 8400,
    });
    const form = initialForm(set);
    expect(form.account).toBe("keep");
    expect(toAnswers(form, set)).toEqual({ where: { network: false, port: 8400 }, who: { shared: false } });
    const edit = withAccount(form, set, "api-key");
    expect(edit.models).toEqual(["haiku"]);
    expect(keyMissing(edit, set)).toBe(false);
    expect(formProblem(edit, set)).toBeNull();
    expect(keyPlace(set.current.connections[0], set)).toContain("the macOS Keychain");
  });

  it("sends a typed key once, with the models and the default", () => {
    const base = info();
    const form = { ...withAccount(initialForm(base), base, "api-key"), apiKey: " sk-ant-xyz ", models: ["haiku", "sonnet"], defaultModel: "haiku" };
    expect(formProblem(form, base)).toBeNull();
    expect(toAnswers(form, base)).toEqual({
      account: { account: "api-key", models: ["sonnet", "haiku"], default_model: "haiku", api_key: "sk-ant-xyz" },
      where: { network: false, port: 8200 },
      who: { shared: false },
    });
  });

  it("says what is missing", () => {
    const base = info();
    const key = withAccount(initialForm(base), base, "api-key");
    expect(formProblem(key, base)).toBe("api-key needs its key.");
    expect(formProblem({ ...key, apiKey: "k", models: [] }, base)).toBe("Tick at least one model.");
    const vertex = withAccount(initialForm(base), base, "vertex");
    expect(formProblem(vertex, base)).toBe("Give the region.");
    expect(formProblem({ ...vertex, region: "us-east5" }, base)).toBe("Give the Google Cloud project id.");
    const gateway = { ...withAccount(initialForm(base), base, "gateway"), apiKey: "k" };
    expect(formProblem(gateway, base)).toContain("gateway’s address");
    expect(formProblem({ ...gateway, baseUrl: "http://127.0.0.1:4000" }, base)).toBe("Name at least one model the gateway routes.");
    const routed = { ...gateway, baseUrl: "http://127.0.0.1:4000", typedModels: "claude-opus, gpt-sol gpt-sol" };
    expect(chosenModels(routed, base)).toEqual(["claude-opus", "gpt-sol"]);
    expect(formProblem(routed, base)).toBeNull();
    expect(formProblem({ ...initialForm(base), port: "80" }, base)).toContain("port");
  });

  it("takes another provider's key with its models, its background model and the switches (step 3n)", () => {
    const base = info();
    const openai = { ...withAccount(initialForm(base), base, "openai"), apiKey: "sk-test" };
    expect(isOtherAccount(base.accounts.find((item) => item.account === "openai"))).toBe(true);
    expect(isOtherAccount(base.accounts.find((item) => item.account === "plan"))).toBe(false);
    expect(formProblem(openai, base)).toBe("Name at least one model.");
    expect(formProblem({ ...openai, typedModels: "gpt-x", baseUrl: "localhost:9" }, base)).toContain("http://");
    const ready = { ...openai, typedModels: "gpt-x, gpt-x-mini", defaultModel: "gpt-x", smallModel: "gpt-x-mini", maxContext: "128000", disableBetas: true };
    expect(formProblem(ready, base)).toBeNull();
    expect(formProblem({ ...ready, maxOutput: "12" }, base)).toContain("output limit");
    expect(toAnswers(ready, base).account).toEqual({
      account: "openai",
      models: ["gpt-x", "gpt-x-mini"],
      default_model: "gpt-x",
      api_key: "sk-test",
      base_url: "",
      small_model: "gpt-x-mini",
      disable_betas: true,
      max_context_tokens: 128000,
      max_output_tokens: null,
    });
    const azure = { ...withAccount(initialForm(base), base, "azure-openai"), apiKey: "k", typedModels: "prod" };
    expect(formProblem(azure, base)).toContain("address of your Azure OpenAI resource");
    expect(formProblem({ ...azure, baseUrl: "https://r.openai.azure.com" }, base)).toContain("API version");
    expect(formProblem({ ...azure, baseUrl: "https://r.openai.azure.com", apiVersion: "2024-10-21", typedModels: "" }, base)).toBe("Name at least one deployment.");
    expect(toAnswers({ ...azure, baseUrl: "https://r.openai.azure.com", apiVersion: "2024-10-21" }, base).account).toMatchObject({ api_version: "2024-10-21", models: ["prod"] });
    // The plan and the other Claude accounts never send the switches.
    expect(toAnswers(withAccount(initialForm(base), base, "plan"), base).account).not.toHaveProperty("disable_betas");
  });

  it("ticks a provider's models into the typed list and filters a long list (step 3n)", () => {
    expect(toggleModel("a, b", "c", true)).toBe("a, b, c");
    expect(toggleModel("a, b", "a", false)).toBe("b");
    expect(toggleModel("a", "a", true)).toBe("a");
    const many = Array.from({ length: 60 }, (_, index) => ({ id: `vendor/model-${index}`, price: index }));
    expect(filterListed(many, "")).toMatchObject({ more: 20 });
    expect(filterListed(many, "MODEL-5").shown.map((model) => model.id)).toEqual(["vendor/model-5", ...Array.from({ length: 10 }, (_, i) => `vendor/model-5${i}`)]);
    expect(priceLabel({ id: "a" })).toBeNull();
    expect(priceLabel({ id: "a", price: 0 })).toBe("free");
    expect(priceLabel({ id: "a", price: 0.15 })).toBe("$0.15 per million tokens in");
    expect(priceLabel({ id: "a", price: 15 })).toBe("$15 per million tokens in");
  });

  it("refuses the plan in a shared studio and asks for the access key", () => {
    const base = info();
    const shared = { ...initialForm(base), shared: true };
    expect(formProblem(shared, base)).toContain("cannot use the Claude plan");
    const keyed = { ...withAccount(shared, base, "api-key"), apiKey: "sk-ant-xyz" };
    expect(needsAccessKey(keyed)).toBe(true);
    expect(formProblem(keyed, base)).toBe("Make or type an access key for the other devices.");
    expect(formProblem({ ...keyed, accessKey: "too short" }, base)).toContain("at least 24");
    const made = makeAccessKey((bytes) => bytes.fill(7));
    expect(made).toHaveLength(32);
    expect(toAnswers({ ...keyed, accessKey: made }, base).who).toEqual({ shared: true, access_key: made });
    expect(formProblem(keyed, info({}, { access_key_set: true }))).toBeNull();
  });

  it("offers no key on the tailnet only for a Tailscale address and a single owner", () => {
    const base = info();
    const form = { ...initialForm(base), network: true, host: "100.101.102.103", openTailnet: true };
    expect(onTailnet("100.64.0.1")).toBe(true);
    expect(onTailnet("192.168.1.20")).toBe(false);
    expect(needsAccessKey(form)).toBe(false);
    expect(toAnswers(form, base).where).toEqual({ network: true, port: 8200, host: "100.101.102.103", open_tailnet: true });
    expect(needsAccessKey({ ...form, host: "192.168.1.20" })).toBe(true);
    expect(toAnswers({ ...form, host: "192.168.1.20" }, base).where?.open_tailnet).toBe(false);
    expect(toAnswers({ ...form, shared: true }, base).where?.open_tailnet).toBe(false);
    expect(formProblem({ ...form, host: "studio.example" }, base)).toContain("private address");
  });

  it("reads typed model names and knows the route", () => {
    expect(parseModels("a, b\nc  a")).toEqual(["a", "b", "c"]);
    expect(parseRoute("/setup")).toEqual({ name: "setup" });
    expect(parseRoute("/setup/")).toEqual({ name: "setup" });
  });
});

describe("a fetched model list", () => {
  const long = { models: Array.from({ length: 412 }, (_, i) => ({ id: `vendor/model-${i}` })), total: 412,
    suggested: ["vendor/model-1", "vendor/model-2"], suggested_small_model: null };
  const short = { models: [{ id: "a" }, { id: "b" }], total: 2, suggested: ["a", "b"], suggested_small_model: null };

  it("says how many were found and suggested", () => {
    expect(listNote(long)).toBe("412 models found; 2 suggested.");
    expect(listNote(short)).toBe("2 models found.");
  });

  it("fills an empty box with the suggestion, never the whole long list, and keeps typed names", () => {
    expect(afterFetch("", short)).toBe("a, b");
    expect(afterFetch("", long)).toBe("vendor/model-1, vendor/model-2");
    expect(afterFetch("", { ...long, suggested: undefined })).toBe("");
    expect(afterFetch("mine", long)).toBe("mine");
  });

  it("reports typed names the list lacks, and none without a list", () => {
    expect(unknownModels("vendor/model-3, made-up", long)).toEqual(["made-up"]);
    expect(unknownModels("made-up", null)).toEqual([]);
  });
});
