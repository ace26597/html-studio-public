// /setup, and the first-run screen: which model account, where the studio runs, who uses it.
// The rules live in setup.ts; the server keeps what is typed (CONTRACT.md, "Install and first run").

import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { errorMessage } from "./api";
import { Icon, Spinner } from "./icons";
import { LitellmNotice, OtherModelsNotice } from "./ModelsView";
import type { Navigate } from "./router";
import {
  accountOf,
  afterFetch,
  chosenModels,
  filterListed,
  formProblem,
  initialForm,
  isOtherAccount,
  keyMissing,
  keyPlace,
  listNote,
  makeAccessKey,
  needsAccessKey,
  onTailnet,
  parseModels,
  priceLabel,
  setupApi,
  toAnswers,
  toggleModel,
  unknownModels,
  withAccount,
  type LitellmStatus,
  type ModelList,
  type SetupForm,
  type SetupInfo,
  type SetupResult,
} from "./setup";
import type { Theme } from "./theme";
import { IconButton } from "./ui";

interface SetupPageProps {
  /** Opened by itself because nothing is configured yet (no way back, a Skip instead). */
  firstRun: boolean;
  navigate: Navigate;
  theme: Theme;
  onToggleTheme: () => void;
  /** Setup was saved or skipped: the app reads the studio's state again. */
  onDone: () => void;
}

interface OptionProps {
  checked: boolean;
  label: string;
  description: ReactNode;
  badge?: string;
  disabled?: boolean;
  onSelect: () => void;
}

function Option({ checked, label, description, badge, disabled, onSelect }: OptionProps) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      disabled={disabled}
      className={`template-card setup-option${checked ? " is-checked" : ""}`}
      onClick={onSelect}
    >
      <span className="template-top">
        <span className="template-label">{label}</span>
        {badge && <span className="badge badge-app">{badge}</span>}
      </span>
      <span className="template-description">{description}</span>
    </button>
  );
}

/** A server sentence with its `commands` set in code. */
function Ticks({ text }: { text: string }) {
  return (
    <>
      {text.split("`").map((part, index) => (index % 2 === 1 ? <code key={index}>{part}</code> : part))}
    </>
  );
}

function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="command">
      <code>{command}</code>
      <button
        type="button"
        className="btn btn-small"
        onClick={() => {
          navigator.clipboard?.writeText(command).then(
            () => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1600);
            },
            () => {},
          );
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

export function SetupPage({ firstRun, navigate, theme, onToggleTheme, onDone }: SetupPageProps) {
  const [info, setInfo] = useState<SetupInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<SetupForm | null>(null);
  const [busy, setBusy] = useState<"save" | "skip" | "review" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [review, setReview] = useState<SetupResult | null>(null);
  const [done, setDone] = useState<SetupResult | null>(null);
  const [copied, setCopied] = useState(false);
  // Step 3n: a provider's own model list (fetched with the key typed here), and the studio's LiteLLM.
  const [listed, setListed] = useState<{ account: string; list: ModelList } | null>(null);
  const [listBusy, setListBusy] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [listQuery, setListQuery] = useState("");
  const [litellm, setLitellm] = useState<LitellmStatus | null>(null);
  const ids = { key: useId(), region: useId(), project: useId(), resource: useId(), address: useId(), small: useId(), models: useId(), def: useId(), host: useId(), port: useId(), access: useId(), version: useId(), context: useId(), output: useId(), filter: useId() };

  // While LiteLLM installs (started by the button below), ask how it is going.
  const installing = litellm?.installing === true;
  useEffect(() => {
    if (!installing) return;
    const timer = window.setInterval(() => {
      setupApi.litellm().then(setLitellm, () => {});
    }, 1500);
    return () => window.clearInterval(timer);
  }, [installing]);

  const load = useCallback(() => {
    setLoadError(null);
    setupApi.get().then(
      (result) => {
        setInfo(result);
        setForm(initialForm(result));
      },
      (cause) => setLoadError(errorMessage(cause)),
    );
  }, []);

  useEffect(() => {
    document.title = "Setup · Studio";
    load();
    return () => {
      document.title = "Studio";
    };
  }, [load]);

  const bar = (
    <header className="home-bar">
      <span className="home-bar-start">
        {!firstRun && <IconButton icon="arrow-left" label="All pages" tipAlign="start" onClick={() => navigate("/")} />}
        <span className="wordmark">
          <span className="wordmark-mark" aria-hidden="true" />
          Studio
        </span>
      </span>
      <IconButton
        icon={theme === "dark" ? "sun" : "moon"}
        label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        tip={theme === "dark" ? "Light theme" : "Dark theme"}
        tipAlign="end"
        onClick={onToggleTheme}
      />
    </header>
  );

  if (!info || !form) {
    return (
      <div className="home setup-page">
        {bar}
        <main className="home-main setup-main">
          {loadError ? (
            <div className="home-empty">
              <h1>Setup couldn’t be opened</h1>
              <p>{loadError}</p>
              <button type="button" className="btn" onClick={load}>
                Try again
              </button>
            </div>
          ) : (
            <div className="home-loading">
              <Spinner size={18} />
            </div>
          )}
        </main>
      </div>
    );
  }

  const set = (patch: Partial<SetupForm>) => {
    setForm((current) => (current ? { ...current, ...patch } : current));
    setError(null);
    setReview(null);
  };
  const account = form.account === "keep" ? undefined : accountOf(info, form.account);
  const existing = account
    ? info.current.connections.find((item) => item.id === account.id && item.kind === account.kind)
    : undefined;
  const claudeAccounts = info.accounts.filter((item) => !isOtherAccount(item));
  const otherAccounts = info.accounts.filter((item) => isOtherAccount(item));
  const other = isOtherAccount(account);
  const lite = litellm ?? info.litellm ?? null;
  const fetched = account && listed?.account === account.account ? listed.list : null;
  const picked = parseModels(form.typedModels);
  const narrowed = fetched ? filterListed(fetched.models, listQuery) : null;
  const models = chosenModels(form, info);
  const unknown = unknownModels(form.typedModels, fetched);

  const pickAccount = (name: string) => {
    setForm(withAccount(form, info, name));
    setError(null);
    setReview(null);
    setListError(null);
    setListQuery("");
  };

  const fetchModels = async () => {
    if (!account) return;
    setListBusy(true);
    setListError(null);
    try {
      const list = await setupApi.listModels(account.account, form.apiKey, form.baseUrl);
      setListed({ account: account.account, list });
      typeModels(afterFetch(form.typedModels, list));
    } catch (cause) {
      setListed(null);
      setListError(errorMessage(cause));
    } finally {
      setListBusy(false);
    }
  };

  const installLitellm = () => {
    setupApi.installLitellm().then(setLitellm, (cause) => setError(errorMessage(cause)));
  };

  const typeModels = (typed: string) => {
    const names = parseModels(typed);
    set({ typedModels: typed, defaultModel: names.includes(form.defaultModel) ? form.defaultModel : (names[0] ?? "") });
  };
  const problem = formProblem(form, info);
  const login = info.detected.claude_login;
  const tailscale = info.detected.tailscale;
  const wantsKey = needsAccessKey(form);

  const finish = (result: SetupResult) => {
    setReview(null);
    if (firstRun && !result.restart_needed) {
      // Nothing to restart for: straight to the studio.
      onDone();
      navigate("/", { replace: true });
      return;
    }
    setDone(result);
    setInfo(result.setup);
    setForm(initialForm(result.setup)); // a typed key is sent once: it does not stay in the form
  };

  const save = async (confirmed: boolean) => {
    if (problem) {
      setError(problem);
      return;
    }
    const answers = toAnswers(form, info);
    try {
      if (info.config_exists && !confirmed) {
        setBusy("review");
        setReview(await setupApi.save(answers, true));
      } else {
        setBusy("save");
        finish(await setupApi.save(answers));
      }
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  };

  const skip = async () => {
    setBusy("skip");
    try {
      await setupApi.save({ skip: true });
      onDone();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(null);
    }
  };

  if (done) {
    return (
      <div className="home setup-page">
        {bar}
        <main className="home-main setup-main">
          <div className="setup-done" role="status">
            <h1>{done.changed ? "Saved" : "Nothing changed"}</h1>
            {done.changes.length > 0 && (
              <ul className="setup-changes">
                {done.changes.map((change) => (
                  <li key={change}>
                    <Ticks text={change} />
                  </li>
                ))}
              </ul>
            )}
            <p className="field-hint">
              Written to <code>{info.config_path}</code>. Keys are kept in {info.secrets.label}, never in that file.
            </p>
            {done.restart_needed && (
              <>
                <p className="notice notice-warning">
                  <Icon name="warning" size={14} />
                  <span>
                    Where the studio listens and who may use it are read when it starts. Restart it from a terminal
                    for this to take effect:
                  </span>
                </p>
                <CopyCommand command={done.restart_command} />
              </>
            )}
            <div className="fullpage-actions">
              <button
                type="button"
                className="btn btn-primary btn-large"
                onClick={() => {
                  onDone();
                  navigate("/", { replace: true });
                }}
              >
                Open the studio
              </button>
              <button type="button" className="btn btn-large" onClick={() => setDone(null)}>
                Change something
              </button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="home setup-page">
      {bar}
      <main className="home-main setup-main">
        <div className="home-head">
          <div>
            <h1>{firstRun ? "Set up the studio" : "Setup"}</h1>
            <p className="home-count">
              Three choices. Each can be changed later, here or with <code>studio setup</code> in a terminal.
            </p>
          </div>
        </div>
        {info.fake && (
          <p className="notice">
            <Icon name="info" size={14} />
            <span>This studio runs the scripted agent, so no model is called while it is on. Setup still saves what you choose.</span>
          </p>
        )}

        <section className="setup-step" aria-labelledby="setup-account">
          <h2 id="setup-account">
            <span className="setup-number" aria-hidden="true">
              1
            </span>
            Which model account should the assistant use?
          </h2>
          <p className={`setup-found${login.found ? " is-found" : ""}`}>
            <Icon name={login.found ? "check" : "info"} size={14} />
            <span>
              <Ticks text={login.detail} />
            </span>
          </p>
          <div className="setup-options" role="radiogroup" aria-labelledby="setup-account">
            {info.current.connections.length > 0 && (
              <Option
                checked={form.account === "keep"}
                label="Keep what is set up now"
                description={info.current.connections.map((item) => `${item.label} (${item.models.join(", ")})`).join(" · ")}
                onSelect={() => setForm(withAccount(form, info, "keep"))}
              />
            )}
            {claudeAccounts.map((item) => (
              <Option
                key={item.account}
                checked={form.account === item.account}
                label={item.label}
                description={item.available ? item.summary : (item.note ?? item.summary)}
                badge={item.account === "plan" && login.found && item.available ? "Found" : undefined}
                disabled={!item.available}
                onSelect={() => pickAccount(item.account)}
              />
            ))}
            {otherAccounts.length > 0 && (
              <p className="setup-group">
                <span>Other models</span>
                <span className="badge badge-experimental">Experimental</span>
                <span className="setup-group-note">{info.other_models?.headline}</span>
              </p>
            )}
            {otherAccounts.map((item) => (
              <Option
                key={item.account}
                checked={form.account === item.account}
                label={item.label}
                description={item.available ? item.summary : (item.note ?? item.summary)}
                disabled={!item.available}
                onSelect={() => pickAccount(item.account)}
              />
            ))}
          </div>

          {other && info.other_models && <OtherModelsNotice note={info.other_models} />}
          {account?.needs_litellm && lite && <LitellmNotice status={lite} onInstall={installLitellm} quiet />}

          {account && (
            <div className="setup-fields">
              {account.key !== "none" && (
                <div className="setup-field setup-field-wide">
                  <label className="field-label" htmlFor={ids.key}>
                    Key {account.key === "optional" && <span className="field-optional">optional</span>}
                  </label>
                  <input
                    id={ids.key}
                    className={`text-input is-mono${error && keyMissing(form, info) ? " is-invalid" : ""}`}
                    type="password"
                    value={form.apiKey}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={existing?.key_from && existing.key_from !== "missing" ? "Leave empty to keep the saved key" : "Paste the key"}
                    onChange={(event) => set({ apiKey: event.target.value })}
                  />
                  <p className="field-hint">
                    {keyPlace(existing, info) ??
                      `It goes to ${info.secrets.label}. It is never written to the config file or shown again.`}
                    {account.key === "optional" ? " Leave it empty to use this computer’s Entra sign-in." : ""}
                  </p>
                </div>
              )}
              {account.fields.includes("base_url") && (
                <div className="setup-field setup-field-wide">
                  <label className="field-label" htmlFor={ids.address}>
                    Address {account.account === "foundry" && <span className="field-optional">instead of a resource name</span>}
                    {account.account === "openai" && <span className="field-optional">optional</span>}
                  </label>
                  <input
                    id={ids.address}
                    className="text-input is-mono"
                    value={form.baseUrl}
                    spellCheck={false}
                    autoCapitalize="off"
                    placeholder={
                      account.account === "gateway"
                        ? "http://127.0.0.1:4000"
                        : account.account === "openai"
                          ? "Empty for OpenAI itself"
                          : account.account === "azure-openai"
                            ? "https://my-resource.openai.azure.com"
                            : "https://<resource>.services.ai.azure.com/anthropic"
                    }
                    onChange={(event) => set({ baseUrl: event.target.value })}
                  />
                  {account.account === "openai" && (
                    <p className="field-hint">
                      Only for another service that speaks OpenAI’s chat format (a local model server, another gateway).
                    </p>
                  )}
                </div>
              )}
              {account.fields.includes("api_version") && (
                <div className="setup-field">
                  <label className="field-label" htmlFor={ids.version}>
                    API version
                  </label>
                  <input
                    id={ids.version}
                    className="text-input is-mono"
                    value={form.apiVersion}
                    spellCheck={false}
                    autoCapitalize="off"
                    placeholder="2024-10-21"
                    onChange={(event) => set({ apiVersion: event.target.value })}
                  />
                </div>
              )}
              {account.fields.includes("resource") && (
                <div className="setup-field">
                  <label className="field-label" htmlFor={ids.resource}>
                    Resource name
                  </label>
                  <input id={ids.resource} className="text-input" value={form.resource} spellCheck={false} onChange={(event) => set({ resource: event.target.value })} />
                </div>
              )}
              {account.fields.includes("region") && (
                <div className="setup-field">
                  <label className="field-label" htmlFor={ids.region}>
                    Region
                  </label>
                  <input
                    id={ids.region}
                    className="text-input"
                    value={form.region}
                    spellCheck={false}
                    autoCapitalize="off"
                    placeholder={account.account === "bedrock" ? "us-east-1" : "us-east5"}
                    onChange={(event) => set({ region: event.target.value })}
                  />
                </div>
              )}
              {account.fields.includes("project") && (
                <div className="setup-field">
                  <label className="field-label" htmlFor={ids.project}>
                    Google Cloud project id
                  </label>
                  <input id={ids.project} className="text-input" value={form.project} spellCheck={false} autoCapitalize="off" onChange={(event) => set({ project: event.target.value })} />
                </div>
              )}

              <div className="setup-field setup-field-wide">
                <span className="field-label" id={ids.models}>
                  {account.account === "azure-openai" ? "Deployments to offer" : "Models to offer"}
                </span>
                {account.models.length > 0 ? (
                  <div className="setup-models" role="group" aria-labelledby={ids.models}>
                    {account.models.map((model) => (
                      <label key={model} className="check-row">
                        <input
                          type="checkbox"
                          checked={form.models.includes(model)}
                          onChange={(event) => {
                            const next = event.target.checked ? [...form.models, model] : form.models.filter((item) => item !== model);
                            const kept = account.models.filter((item) => next.includes(item));
                            set({ models: next, defaultModel: kept.includes(form.defaultModel) ? form.defaultModel : (kept[0] ?? "") });
                          }}
                        />
                        <span>{model.charAt(0).toUpperCase() + model.slice(1)}</span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <>
                    <input
                      className="text-input is-mono"
                      aria-labelledby={ids.models}
                      value={form.typedModels}
                      spellCheck={false}
                      autoCapitalize="off"
                      placeholder={account.needs_litellm ? "The names, separated by commas" : "claude-opus, gpt-sol"}
                      onChange={(event) => typeModels(event.target.value)}
                    />
                    {picked.length > 0 && (
                      <ul className="setup-picked" aria-label="Models picked">
                        {picked.map((name) => (
                          <li key={name} className="chip">
                            <span className="chip-text">{name}</span>
                            <button type="button" className="chip-remove" aria-label={`Remove ${name}`} onClick={() => typeModels(toggleModel(form.typedModels, name, false))}>
                              <Icon name="x" size={11} strokeWidth={2.2} />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    {unknown.length > 0 && (
                      <p className="field-hint is-error" role="status">
                        Not in the fetched list: {unknown.join(", ")}. They are kept; check the spelling.
                      </p>
                    )}
                    <p className="field-hint">
                      {account.account === "azure-openai"
                        ? "The names of your deployments, exactly as Azure shows them, separated by commas. Azure has no list of them that a key can read."
                        : account.needs_litellm
                          ? "The names exactly as the provider calls them, separated by commas."
                          : "The names exactly as the gateway calls them, separated by commas."}
                    </p>
                    {account.lists_models && (
                      <div className="setup-list">
                        <div className="setup-list-head">
                          <button type="button" className="btn btn-small" onClick={() => void fetchModels()} disabled={listBusy}>
                            {listBusy && <Spinner size={12} />}
                            {fetched ? "Fetch the list again" : account.needs_litellm ? "Fetch the list from the provider" : "Fetch the list from the gateway"}
                          </button>
                          {fetched && (
                            <>
                              <label className="sr-only" htmlFor={ids.filter}>
                                Filter the provider’s models
                              </label>
                              <input
                                id={ids.filter}
                                className="text-input setup-list-filter"
                                type="search"
                                value={listQuery}
                                placeholder={`Filter ${fetched.models.length} models`}
                                onChange={(event) => setListQuery(event.target.value)}
                              />
                            </>
                          )}
                        </div>
                        {fetched && <p className="field-hint">{listNote(fetched)} Only the picked ones are saved; tick, untick or edit the names above.</p>}
                        {listError && (
                          <p className="field-hint is-error" role="alert">
                            {listError}
                          </p>
                        )}
                        {narrowed && (
                          <div className="setup-list-rows" role="group" aria-label="The provider’s models">
                            {narrowed.shown.map((model) => (
                              <label key={model.id} className="check-row">
                                <input
                                  type="checkbox"
                                  checked={picked.includes(model.id)}
                                  onChange={(event) => typeModels(toggleModel(form.typedModels, model.id, event.target.checked))}
                                />
                                <span className="setup-list-id">{model.id}</span>
                                {priceLabel(model) && <span className="setup-list-price">{priceLabel(model)}</span>}
                              </label>
                            ))}
                            {narrowed.shown.length === 0 && <p className="field-hint">No model matches.</p>}
                            {narrowed.more > 0 && <p className="field-hint">{narrowed.more} more: type a word to narrow the list.</p>}
                          </div>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
              {models.length > 1 && (
                <div className="setup-field">
                  <label className="field-label" htmlFor={ids.def}>
                    Default model
                  </label>
                  <select id={ids.def} className="text-input" value={models.includes(form.defaultModel) ? form.defaultModel : models[0]} onChange={(event) => set({ defaultModel: event.target.value })}>
                    {models.map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {account.fields.includes("small_model") && (
                <div className="setup-field">
                  <label className="field-label" htmlFor={ids.small}>
                    Cheap model for background work and picking skills <span className="field-optional">optional</span>
                  </label>
                  <input
                    id={ids.small}
                    className="text-input is-mono"
                    value={form.smallModel}
                    spellCheck={false}
                    autoCapitalize="off"
                    placeholder={account.needs_litellm ? (fetched?.suggested_small_model ?? "The cheapest of the models above") : ""}
                    onChange={(event) => set({ smallModel: event.target.value })}
                  />
                  {(account.needs_litellm || account.kind === "anthropic-gateway") && (
                    <p className="field-hint">
                      Titles, summaries, picking skills and other small jobs go to this model, on this same account.
                      Empty: the studio takes the cheapest-looking of the models above, and says which when you save.
                    </p>
                  )}
                </div>
              )}
              {other && (
                <details className="setup-advanced setup-field-wide">
                  <summary>Only if a model misbehaves</summary>
                  <div className="setup-fields">
                    <div className="setup-field">
                      <label className="field-label" htmlFor={ids.context}>
                        Context limit, in tokens <span className="field-optional">optional</span>
                      </label>
                      <input id={ids.context} className="text-input is-mono" value={form.maxContext} inputMode="numeric" placeholder="For example 128000" onChange={(event) => set({ maxContext: event.target.value.replace(/[^\d]/g, "") })} />
                    </div>
                    <div className="setup-field">
                      <label className="field-label" htmlFor={ids.output}>
                        Output limit, in tokens <span className="field-optional">optional</span>
                      </label>
                      <input id={ids.output} className="text-input is-mono" value={form.maxOutput} inputMode="numeric" placeholder="For example 16000" onChange={(event) => set({ maxOutput: event.target.value.replace(/[^\d]/g, "") })} />
                    </div>
                    <label className="check-row setup-field-wide">
                      <input type="checkbox" checked={form.disableBetas} onChange={(event) => set({ disableBetas: event.target.checked })} />
                      <span>
                        Leave out Anthropic’s experimental request fields. For a provider that refuses them; it also turns
                        off the assistant’s search for tools, so keep it off unless requests fail.
                      </span>
                    </label>
                  </div>
                </details>
              )}
              {account.account === "bedrock" && (
                <p className="field-hint setup-field-wide">The studio uses the AWS sign-in it is started with (a profile or keys in its environment).</p>
              )}
              {account.account === "vertex" && (
                <p className="field-hint setup-field-wide">The studio uses Google’s application default credentials (<code>gcloud auth application-default login</code>).</p>
              )}
            </div>
          )}
        </section>

        <section className="setup-step" aria-labelledby="setup-where">
          <h2 id="setup-where">
            <span className="setup-number" aria-hidden="true">
              2
            </span>
            Where should the studio run?
          </h2>
          <div className="setup-options" role="radiogroup" aria-labelledby="setup-where">
            <Option
              checked={!form.network}
              label="This computer only"
              description="Nothing else can reach it."
              badge="Default"
              onSelect={() => set({ network: false })}
            />
            <Option
              checked={form.network}
              label="Also on a private network address"
              description={tailscale.detail}
              onSelect={() => set({ network: true, host: form.host || tailscale.address || "" })}
            />
          </div>
          <div className="setup-fields">
            {form.network && (
              <>
                <p className="notice notice-warning setup-field-wide">
                  <Icon name="warning" size={14} />
                  <span>{info.network_warning}</span>
                </p>
                <div className="setup-field">
                  <label className="field-label" htmlFor={ids.host}>
                    Address to listen on
                  </label>
                  <input
                    id={ids.host}
                    className="text-input is-mono"
                    value={form.host}
                    inputMode="decimal"
                    spellCheck={false}
                    placeholder="100.101.102.103"
                    onChange={(event) => set({ host: event.target.value })}
                  />
                </div>
              </>
            )}
            <div className="setup-field">
              <label className="field-label" htmlFor={ids.port}>
                Port
              </label>
              <input id={ids.port} className="text-input is-mono" value={form.port} inputMode="numeric" onChange={(event) => set({ port: event.target.value.replace(/[^\d]/g, "") })} />
              <p className="field-hint">Project pages use the 99 ports after it.</p>
            </div>
            {form.network && onTailnet(form.host) && !form.shared && (
              <div className="setup-field setup-field-wide">
                <span className="field-label">Devices on your Tailscale network</span>
                <label className="check-row">
                  <input type="radio" name="tailnet" checked={!form.openTailnet} onChange={() => set({ openTailnet: false })} />
                  <span>Ask each device for the access key once</span>
                </label>
                <label className="check-row">
                  <input type="radio" name="tailnet" checked={form.openTailnet} onChange={() => set({ openTailnet: true })} />
                  <span>Let them in without a key (your Tailscale network is then the only boundary)</span>
                </label>
              </div>
            )}
          </div>
        </section>

        <section className="setup-step" aria-labelledby="setup-who">
          <h2 id="setup-who">
            <span className="setup-number" aria-hidden="true">
              3
            </span>
            Who uses it?
          </h2>
          <div className="setup-options" role="radiogroup" aria-labelledby="setup-who">
            <Option checked={!form.shared} label="Just me" description="Your own Claude plan may be used." badge="Default" onSelect={() => set({ shared: false })} />
            <Option
              checked={form.shared}
              label="Shared with other people"
              description="The Claude plan is switched off (Anthropic does not allow a tool to offer plan sign-in to its users). Everyone signs in with the access key."
              onSelect={() => set({ shared: true })}
            />
          </div>
          {wantsKey && (
            <div className="setup-fields">
              <div className="setup-field setup-field-wide">
                <label className="field-label" htmlFor={ids.access}>
                  Access key for other devices
                </label>
                <div className="attach-path-row">
                  <input
                    id={ids.access}
                    className="text-input is-mono"
                    value={form.accessKey}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={info.current.access_key_set ? "Leave empty to keep the saved one" : "At least 24 characters"}
                    onChange={(event) => set({ accessKey: event.target.value })}
                  />
                  {form.accessKey.trim() ? (
                    <button
                      type="button"
                      className="btn"
                      onClick={() => {
                        navigator.clipboard?.writeText(form.accessKey.trim()).then(
                          () => {
                            setCopied(true);
                            window.setTimeout(() => setCopied(false), 1600);
                          },
                          () => {},
                        );
                      }}
                    >
                      {copied ? "Copied" : "Copy"}
                    </button>
                  ) : (
                    <button type="button" className="btn" onClick={() => set({ accessKey: makeAccessKey() })}>
                      Make one
                    </button>
                  )}
                </div>
                <p className="field-hint">
                  {form.accessKey ? (
                    <>
                      Copy it now: each device enters it once, and it is not shown again here. <code>studio access-key</code>{" "}
                      in a terminal prints it.
                    </>
                  ) : (
                    `It is kept in ${info.secrets.label}. Each device enters it once.`
                  )}
                </p>
              </div>
            </div>
          )}
        </section>

        {review && (
          <section className="setup-review" aria-label="What will change">
            <h2>{review.changed ? "This will change" : "Nothing changes"}</h2>
            {review.changes.length > 0 && (
              <ul className="setup-changes">
                {review.changes.map((change) => (
                  <li key={change}>
                    <Ticks text={change} />
                  </li>
                ))}
              </ul>
            )}
            {review.diff && <pre className="setup-diff">{review.diff}</pre>}
            <p className="field-hint">
              The file is <code>{info.config_path}</code>; the one it replaces is kept beside it as <code>config.json.bak</code>.
            </p>
          </section>
        )}

        <footer className="setup-actions">
          {error && (
            <p className="notice notice-error" role="alert">
              <Icon name="alert" size={14} />
              <span>{error}</span>
            </p>
          )}
          <div className="setup-buttons">
            {firstRun && (
              <button type="button" className="btn btn-large" onClick={() => void skip()} disabled={busy !== null}>
                {busy === "skip" && <Spinner size={14} />}
                Skip for now
              </button>
            )}
            {review ? (
              <button type="button" className="btn btn-primary btn-large" onClick={() => void save(true)} disabled={busy !== null || !review.changed}>
                {busy === "save" && <Spinner size={14} />}
                Write it
              </button>
            ) : (
              <button type="button" className="btn btn-primary btn-large" onClick={() => void save(false)} disabled={busy !== null}>
                {(busy === "save" || busy === "review") && <Spinner size={14} />}
                {info.config_exists ? "Review changes" : firstRun ? "Save and open the studio" : "Save"}
              </button>
            )}
          </div>
        </footer>
      </main>
    </div>
  );
}
