// Setup (step 3m): the first-run screen's types, API calls and pure form rules.
// CONTRACT.md, "Install and first run". The same answers drive `studio setup` in the terminal.

import { request } from "./api";

export type AccountName = string;

/** One row of "which model account": what setup offers, or will offer. */
export interface SetupAccount {
  account: AccountName;
  kind: string | null;
  id: string | null;
  label: string;
  summary: string;
  key: "none" | "required" | "optional";
  fields: string[];
  models: string[];
  default_model: string | null;
  available: boolean;
  note: string | null;
  /** Step 3n. `other`: models that are not Claude's (experimental); `claude` otherwise. */
  group?: "claude" | "other";
  /** Step 3n. Set for the four accounts the studio runs its own LiteLLM for. */
  provider?: string | null;
  /** Step 3n. The provider can be asked for its model list with the key. */
  lists_models?: boolean;
  /** Step 3n. The account runs through the studio's LiteLLM, which may still have to be installed. */
  needs_litellm?: boolean;
}

/** One model of a provider's own list; `price` is dollars per million input tokens. */
export interface ListedModel {
  id: string;
  price?: number;
}

export interface ModelList {
  models: ListedModel[];
  /** How many the provider or gateway listed, and the short list setup suggests (CONTRACT.md). */
  total?: number;
  suggested?: string[];
  suggested_small_model: string | null;
}

/** The studio's own LiteLLM, as `GET /api/litellm` describes it. */
export interface LitellmStatus {
  installed: boolean;
  version: string;
  pinned: string;
  needed: boolean;
  connections: string[];
  state: "not_installed" | "idle" | "running" | "stopped";
  detail: string;
  port: number | null;
  last_exit: { code: number; at: string; tail: string } | null;
  log: string;
  install_command: string;
  size: string;
  installing: boolean;
  install_error: string | null;
  install_log: string[];
}

/** What is said wherever a model that is not Claude's is chosen. */
export interface OtherModelsNote {
  headline: string;
  limits: string[];
}

export interface SetupConnection {
  id: string;
  kind: string;
  label: string;
  account: AccountName | null;
  models: string[];
  default_model: string | null;
  /** Where its key is: the keychain or the owner-only file, an environment variable, or nowhere. */
  key_from: "keychain" | "file" | "memory" | "environment" | "missing" | null;
  api_key_env?: string | null;
  base_url?: string;
  region?: string;
  project?: string;
  resource?: string;
  small_model?: string;
  provider?: string;
  api_version?: string;
  disable_betas?: boolean;
  max_context_tokens?: number;
  max_output_tokens?: number;
}

export interface SetupInfo {
  needed: boolean;
  fake: boolean;
  version: string;
  config_path: string;
  config_exists: boolean;
  secrets: { backend: string; label: string };
  detected: {
    claude_login: { found: boolean; plan: string | null; detail: string };
    tailscale: { found: boolean; address: string | null; detail: string };
  };
  accounts: SetupAccount[];
  /** Step 3n. */
  other_models?: OtherModelsNote;
  /** Step 3n. Whether the LiteLLM the four provider accounts run through is installed. */
  litellm?: { installed: boolean; version: string; size: string; install_command: string };
  current: {
    connections: SetupConnection[];
    port: number;
    hosts: string[];
    open_tailnet: boolean;
    shared: boolean;
    access_key_set: boolean;
    done: boolean;
  };
  running: { port: number; hosts: string[]; open_tailnet: boolean; shared: boolean; https: boolean };
  network_warning: string;
  restart_command: string;
}

export interface SetupAnswers {
  account?: Record<string, unknown>;
  where?: { network?: boolean; host?: string; port?: number; open_tailnet?: boolean };
  who?: { shared?: boolean; access_key?: string };
  skip?: boolean;
}

export interface SetupResult {
  applied: boolean;
  changed: boolean;
  changes: string[];
  diff: string;
  restart_needed: boolean;
  restart_command: string;
  setup: SetupInfo;
}

export const setupApi = {
  get: (signal?: AbortSignal) => request<SetupInfo>("GET", "/setup", undefined, signal),
  save: (answers: SetupAnswers, dryRun = false) =>
    request<SetupResult>("POST", "/setup", { answers, dry_run: dryRun }),
  /** The provider's own list of models, asked for with the key just typed (used once, not kept). */
  listModels: (account: string, apiKey: string, baseUrl: string) =>
    request<ModelList>("POST", "/setup/models", {
      account,
      api_key: apiKey.trim() || null,
      base_url: baseUrl.trim() || null,
    }),
  litellm: (signal?: AbortSignal) => request<LitellmStatus>("GET", "/litellm", undefined, signal),
  installLitellm: () => request<LitellmStatus>("POST", "/litellm/install"),
};

export const MIN_ACCESS_KEY = 24;

export interface SetupForm {
  /** The chosen account, or "keep" to leave the model accounts as they are. */
  account: AccountName | "keep";
  apiKey: string;
  region: string;
  project: string;
  resource: string;
  baseUrl: string;
  smallModel: string;
  /** Azure OpenAI's API version. */
  apiVersion: string;
  /** For a provider that rejects Anthropic's beta request fields (also turns off tool search). */
  disableBetas: boolean;
  maxContext: string;
  maxOutput: string;
  /** Models ticked from the account's list. */
  models: string[];
  /** Model names typed for a gateway, separated by commas or spaces. */
  typedModels: string;
  defaultModel: string;
  network: boolean;
  host: string;
  port: string;
  openTailnet: boolean;
  shared: boolean;
  accessKey: string;
}

export function accountOf(info: SetupInfo, name: string): SetupAccount | undefined {
  return info.accounts.find((item) => item.account === name);
}

/** Model names from a typed list: commas, spaces or new lines between them, no repeats. */
export function parseModels(text: string): string[] {
  const seen: string[] = [];
  for (const part of text.split(/[\s,]+/)) {
    if (part && !seen.includes(part)) seen.push(part);
  }
  return seen;
}

/** The models the form offers for its account: ticked ones, or the typed list for a gateway. */
export function chosenModels(form: SetupForm, info: SetupInfo): string[] {
  const account = accountOf(info, form.account);
  if (!account) return [];
  return account.models.length > 0 ? account.models.filter((model) => form.models.includes(model)) : parseModels(form.typedModels);
}

/** What the screen starts with: what is configured, else the plan when a login was found, else a key. */
export function initialForm(info: SetupInfo): SetupForm {
  const current = info.current;
  const first = current.connections[0];
  const usable = (name: string) => accountOf(info, name)?.available === true;
  let account: SetupForm["account"];
  if (first) account = "keep";
  else if (info.detected.claude_login.found && usable("plan") && !current.shared) account = "plan";
  else account = "api-key";
  const form: SetupForm = {
    account,
    apiKey: "",
    region: "",
    project: "",
    resource: "",
    baseUrl: "",
    smallModel: "",
    apiVersion: "",
    disableBetas: false,
    maxContext: "",
    maxOutput: "",
    models: [],
    typedModels: "",
    defaultModel: "",
    network: current.hosts.length > 0,
    host: current.hosts[0] ?? info.detected.tailscale.address ?? "",
    port: String(current.port),
    openTailnet: current.open_tailnet,
    shared: current.shared,
    accessKey: "",
  };
  return account === "keep" ? form : withAccount(form, info, account);
}

/** Switch the account: its own models and default, and the fields of an account of that kind already set up. */
export function withAccount(form: SetupForm, info: SetupInfo, name: SetupForm["account"]): SetupForm {
  if (name === "keep") return { ...form, account: "keep", apiKey: "" };
  const account = accountOf(info, name);
  if (!account) return form;
  const existing = info.current.connections.find((item) => item.id === account.id && item.kind === account.kind);
  const known = existing?.models.filter((model) => account.models.includes(model)) ?? [];
  const models = account.models.length === 0 ? [] : known.length > 0 ? known : account.models;
  return {
    ...form,
    account: name,
    apiKey: "",
    region: existing?.region ?? "",
    project: existing?.project ?? "",
    resource: existing?.resource ?? "",
    baseUrl: existing?.base_url ?? "",
    smallModel: existing?.small_model ?? "",
    apiVersion: existing?.api_version ?? "",
    disableBetas: existing?.disable_betas === true,
    maxContext: existing?.max_context_tokens ? String(existing.max_context_tokens) : "",
    maxOutput: existing?.max_output_tokens ? String(existing.max_output_tokens) : "",
    models,
    typedModels: account.models.length === 0 ? (existing?.models ?? []).join(", ") : "",
    defaultModel: existing?.default_model ?? account.default_model ?? models[0] ?? "",
  };
}

/** An account for models that are not Claude's: the four provider keys and the gateway. */
export function isOtherAccount(account: SetupAccount | undefined): boolean {
  return account?.group === "other";
}

/** The typed model list with one name added or removed (ticking a fetched model). */
export function toggleModel(typed: string, model: string, on: boolean): string {
  const names = parseModels(typed);
  const next = on ? (names.includes(model) ? names : [...names, model]) : names.filter((name) => name !== model);
  return next.join(", ");
}

/** Fetched models whose name contains every word of `query`; at most `limit`, and how many more there are. */
export function filterListed(models: ListedModel[], query: string, limit = 40): { shown: ListedModel[]; more: number } {
  const words = query.toLowerCase().split(" ").filter(Boolean);
  const matching = models.filter((model) => words.every((word) => model.id.toLowerCase().includes(word)));
  return { shown: matching.slice(0, limit), more: Math.max(0, matching.length - limit) };
}

/** Above this many, a fetched list is narrowed to a suggestion (the server's `gateway.LONG_LIST`). */
export const LONG_LIST = 12;

/** "412 models found; 8 suggested." or "3 models found." */
export function listNote(list: ModelList): string {
  const total = list.total ?? list.models.length;
  const suggested = list.suggested ?? [];
  return total > LONG_LIST ? `${total} models found; ${suggested.length} suggested.` : `${total} models found.`;
}

/** The typed names a fetched list lacks: said to the person, never dropped. */
export function unknownModels(typed: string, list: ModelList | null): string[] {
  if (!list || list.models.length === 0) return [];
  const known = new Set(list.models.map((model) => model.id));
  return parseModels(typed).filter((name) => !known.has(name));
}

/** What fills the models box after a fetch: what the person already named, else the suggestion. */
export function afterFetch(typed: string, list: ModelList): string {
  if (parseModels(typed).length > 0) return typed;
  return (list.suggested ?? (list.models.length <= LONG_LIST ? list.models.map((model) => model.id) : [])).join(", ");
}

/** "$0.15 per million tokens in", "free", or nothing when the provider gave no price. */
export function priceLabel(model: ListedModel): string | null {
  if (typeof model.price !== "number") return null;
  if (model.price === 0) return "free";
  return `$${model.price < 10 ? model.price.toFixed(2) : model.price.toFixed(0)} per million tokens in`;
}

/** True when the address is in the range Tailscale gives its devices (100.64.0.0/10). */
export function onTailnet(address: string): boolean {
  const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(address.trim());
  if (!match) return false;
  const [a, b] = [Number(match[1]), Number(match[2])];
  return a === 100 && b >= 64 && b <= 127 && Number(match[3]) <= 255 && Number(match[4]) <= 255;
}

/** Whether other devices will have to sign in with the access key under these choices. */
export function needsAccessKey(form: SetupForm): boolean {
  return form.shared || (form.network && !(form.openTailnet && onTailnet(form.host)));
}

/** Whether a key has to be typed: one is needed and none is kept for this account already. */
export function keyMissing(form: SetupForm, info: SetupInfo): boolean {
  const account = accountOf(info, form.account);
  if (!account || account.key !== "required" || form.apiKey.trim()) return false;
  const existing = info.current.connections.find((item) => item.id === account.id && item.kind === account.kind);
  return !(existing && existing.key_from && existing.key_from !== "missing");
}

/** The first thing that stops the form from being saved, as a sentence; null when it can be. */
export function formProblem(form: SetupForm, info: SetupInfo): string | null {
  if (form.account === "keep") {
    if (form.shared && !info.current.connections.some((item) => item.kind !== "claude-subscription")) {
      return "A shared studio cannot use the Claude plan. Choose an API key or a cloud account.";
    }
  } else {
    const account = accountOf(info, form.account);
    if (!account || !account.available) return account?.note ?? "Choose a model account.";
    if (account.kind === "claude-subscription" && form.shared) {
      return "A shared studio cannot use the Claude plan. Choose an API key or a cloud account.";
    }
    if (keyMissing(form, info)) return `${account.label} needs its key.`;
    if (account.fields.includes("region") && !form.region.trim()) return "Give the region.";
    if (account.fields.includes("project") && !form.project.trim()) return "Give the Google Cloud project id.";
    if (account.account === "foundry" && !form.resource.trim() && !form.baseUrl.trim()) {
      return "Give the Foundry resource name or its address.";
    }
    if (account.account === "gateway" && !/^https?:\/\/[^\s/]+/.test(form.baseUrl.trim())) {
      return "Give the gateway’s address, for example http://127.0.0.1:4000.";
    }
    const address = form.baseUrl.trim();
    const isAddress = address.startsWith("http://") || address.startsWith("https://");
    if (account.account === "azure-openai") {
      if (!isAddress) {
        return "Give the address of your Azure OpenAI resource, for example https://my-resource.openai.azure.com.";
      }
      if (!form.apiVersion.trim()) return "Give the API version, for example 2024-10-21.";
    } else if (account.needs_litellm && address && !isAddress) {
      return "The address must start with http:// or https://.";
    }
    for (const [value, name] of [
      [form.maxContext, "The context limit"],
      [form.maxOutput, "The output limit"],
    ] as const) {
      if (value.trim() && !(Number.isInteger(Number(value)) && Number(value) >= 1000)) {
        return `${name} is a whole number of tokens, 1000 or more (or empty).`;
      }
    }
    const models = chosenModels(form, info);
    if (models.length === 0) {
      if (account.models.length > 0) return "Tick at least one model.";
      if (account.account === "azure-openai") return "Name at least one deployment.";
      return account.needs_litellm ? "Name at least one model." : "Name at least one model the gateway routes.";
    }
  }
  const port = Number(form.port);
  if (!Number.isInteger(port) || port < 1024 || port > 65300) return "The port is a number between 1024 and 65300.";
  if (form.network && !/^\d{1,3}(\.\d{1,3}){3}$/.test(form.host.trim())) {
    return "Give the private address to listen on, for example 100.101.102.103.";
  }
  if (needsAccessKey(form)) {
    const typed = form.accessKey.trim();
    if (typed && (typed.length < MIN_ACCESS_KEY || /\s/.test(typed))) {
      return `The access key must be at least ${MIN_ACCESS_KEY} characters, with no spaces.`;
    }
    if (!typed && !info.current.access_key_set) return "Make or type an access key for the other devices.";
  }
  return null;
}

/** The answers `POST /api/setup` takes. A key is sent once and never comes back. */
export function toAnswers(form: SetupForm, info: SetupInfo): SetupAnswers {
  const answers: SetupAnswers = {};
  const account = form.account === "keep" ? undefined : accountOf(info, form.account);
  if (account) {
    const models = chosenModels(form, info);
    const body: Record<string, unknown> = { account: account.account, models };
    if (models.includes(form.defaultModel)) body.default_model = form.defaultModel;
    if (form.apiKey.trim()) body.api_key = form.apiKey.trim();
    const fields: Record<string, string> = {
      region: form.region,
      project: form.project,
      resource: form.resource,
      base_url: form.baseUrl,
      small_model: form.smallModel,
      api_version: form.apiVersion,
    };
    for (const name of account.fields) body[name] = fields[name]?.trim() ?? "";
    if (isOtherAccount(account)) {
      body.disable_betas = form.disableBetas;
      body.max_context_tokens = form.maxContext.trim() ? Number(form.maxContext) : null;
      body.max_output_tokens = form.maxOutput.trim() ? Number(form.maxOutput) : null;
    }
    answers.account = body;
  }
  const where: SetupAnswers["where"] = { network: form.network, port: Number(form.port) };
  if (form.network) {
    where.host = form.host.trim();
    where.open_tailnet = !form.shared && form.openTailnet && onTailnet(form.host);
  }
  answers.where = where;
  answers.who = { shared: form.shared };
  if (needsAccessKey(form) && form.accessKey.trim()) answers.who.access_key = form.accessKey.trim();
  return answers;
}

const KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";

/** A new access key made in the browser, so the server never has to send one back. */
export function makeAccessKey(
  random: (bytes: Uint8Array<ArrayBuffer>) => Uint8Array = (bytes) => crypto.getRandomValues(bytes),
): string {
  const bytes = random(new Uint8Array(new ArrayBuffer(32)));
  let key = "";
  for (const byte of bytes) key += KEY_ALPHABET[byte % KEY_ALPHABET.length];
  return key;
}

/** Where a saved key is, in words. */
export function keyPlace(connection: SetupConnection | undefined, info: SetupInfo): string | null {
  if (!connection?.key_from) return null;
  if (connection.key_from === "environment") return `Its key comes from the environment variable ${connection.api_key_env}.`;
  if (connection.key_from === "missing") return "Its key is no longer saved on this computer; type it again.";
  return `A key is already saved in ${info.secrets.label}. Leave this empty to keep it.`;
}
