// Connectors (MCP servers) in the Library: your own, the ones on this computer, and the studio's suggestions.
// Each can be tested; yours can be edited and deleted. Secret values are write-only.

import { Fragment, useEffect, useId, useRef, useState } from "react";
import { api, errorMessage } from "./api";
import { relativeTime } from "./format";
import { Icon, Spinner } from "./icons";
import { groupMcp, matchesQuery, mcpTarget } from "./library";
import {
  emptyMcpForm,
  mcpFormFromServer,
  mcpInputFromForm,
  mcpPatchFromForm,
  secretRow,
  validateMcpForm,
  type McpFieldError,
  type McpForm,
} from "./mcpServerForm";
import { Modal } from "./Modal";
import {
  answerIsEmpty,
  checkCaution,
  checkLine,
  formFromSuggestion,
  leftOutLine,
  MAX_SUGGEST_TEXT,
  SIGN_IN_NOTE,
  signInCommands,
  SUGGEST_EXAMPLE,
  suggestedFormNote,
  suggestedHeading,
  suggestionTarget,
  suggestProblem,
} from "./connectorSuggest";
import { ConnectorStateNote } from "./LibraryControls";
import { connectorState } from "./libraryPick";
import type { McpServer, McpSuggestAnswer, McpSuggestion, McpTestResult } from "./types";
import { useConfirm, useToast } from "./ui";

export function transportLabel(transport: string): string {
  return transport === "stdio" ? "Command" : "Web address";
}

export function serverMatches(server: McpServer, query: string): boolean {
  return matchesQuery(
    [server.label, server.name, server.description, mcpTarget(server), transportLabel(server.transport), ...(server.secret_names ?? [])],
    query,
  );
}

function ToolList({ tools }: { tools: { name: string; description: string }[] }) {
  if (!tools.length) return null;
  return (
    <details className="mcp-tools">
      <summary>
        {tools.length} {tools.length === 1 ? "tool" : "tools"}
      </summary>
      <ul>
        {tools.map((tool) => (
          <li key={tool.name}>
            <code>{tool.name}</code>
            {tool.description && <span> {tool.description}</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

function TestLine({ result, at, now }: { result: McpTestResult | McpServer["last_test"]; at: string | null; now: number }) {
  if (!result) return <span className="ds-last is-none">Not tested yet</span>;
  const count = result.tools?.length ?? 0;
  const when = at ? ` ${relativeTime(at, now)}` : "";
  return (
    <div className="mcp-test">
      <span className={`ds-last ${result.ok ? "is-ok" : "is-fail"}`} role={result.ok ? undefined : "note"}>
        {result.ok
          ? `Connected${when} · ${count} ${count === 1 ? "tool" : "tools"}`
          : `Failed${when}: ${result.message}`}
      </span>
      {result.ok && <ToolList tools={result.tools ?? []} />}
    </div>
  );
}

// ---- add / edit dialog -------------------------------------------------------------

export function McpServerDialog({
  server,
  onSaved,
  onClose,
}: {
  server: McpServer | null;
  onSaved: (server: McpServer) => void;
  onClose: () => void;
}) {
  // Adding starts at "Describe what you need"; editing goes straight to the form, as before.
  const [step, setStep] = useState<"describe" | "form">(server ? "form" : "describe");
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<McpSuggestAnswer | null>(null);
  /** The suggestion the form was filled from (its note is shown above the form). */
  const [used, setUsed] = useState<McpSuggestion | null>(null);
  const asked = useRef<AbortController | null>(null);
  useEffect(() => () => asked.current?.abort(), []);
  const [form, setForm] = useState<McpForm>(() => (server ? mcpFormFromServer(server) : emptyMcpForm()));
  const [fieldError, setFieldError] = useState<McpFieldError | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const ids = useId();
  const id = (field: string) => `${ids}-${field}`;
  const update = (patch: Partial<McpForm>) => {
    setForm((current) => ({ ...current, ...patch }));
    setFieldError(null);
  };
  const updateRow = (key: number, patch: Partial<{ name: string; value: string }>) =>
    update({ secrets: form.secrets.map((row) => (row.key === key ? { ...row, ...patch } : row)) });

  const save = async () => {
    const problem = validateMcpForm(form, server);
    if (problem) {
      setFieldError(problem);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (server) {
        const patch = mcpPatchFromForm(form, server);
        onSaved(Object.keys(patch).length ? await api.patchMcpServer(server.id, patch) : server);
      } else {
        onSaved(await api.createMcpServer(mcpInputFromForm(form)));
      }
    } catch (cause) {
      setError(errorMessage(cause));
      setSaving(false);
    }
  };

  const suggest = async () => {
    const issue = suggestProblem(text);
    if (issue) {
      setProblem(issue);
      return;
    }
    asked.current?.abort();
    const controller = new AbortController();
    asked.current = controller;
    setAsking(true);
    setAskError(null);
    setAnswer(null);
    try {
      setAnswer(await api.suggestMcpServers({ text: text.trim() }, controller.signal));
    } catch (cause) {
      if ((cause as Error)?.name !== "AbortError") setAskError(errorMessage(cause));
    } finally {
      if (asked.current === controller) setAsking(false);
    }
  };

  const toForm = (suggestion: McpSuggestion | null) => {
    setUsed(suggestion);
    setForm(suggestion ? formFromSuggestion(suggestion) : emptyMcpForm());
    setFieldError(null);
    setError(null);
    setStep("form");
  };

  if (!server && step === "describe") {
    return (
      <Modal title="Add a connector" onClose={onClose} wide>
        <DescribeStep
          text={text}
          problem={problem}
          asking={asking}
          error={askError}
          answer={answer}
          onText={(value) => {
            setText(value);
            setProblem(null);
          }}
          onSuggest={() => void suggest()}
          onForm={() => toForm(null)}
          onUse={toForm}
        />
      </Modal>
    );
  }

  const secretsWord = form.transport === "http" ? "Headers" : "Environment";
  const invalid = (field: McpFieldError["field"]) => (fieldError?.field === field ? true : undefined);
  const describe = (field: McpFieldError["field"]) => (fieldError?.field === field ? id("error") : undefined);

  return (
    <Modal title={server ? `Edit ${server.label || server.name}` : "Add a connector"} onClose={onClose} busy={saving} wide>
      {!server && (
        <div className="suggest-form-note">
          {used && <p className="ds-note">{suggestedFormNote(used, answer?.asked?.model)}</p>}
          <button type="button" className="btn btn-small" disabled={saving} onClick={() => setStep("describe")}>
            <Icon name="arrow-left" size={13} /> {answer ? "Back to the suggestions" : "Back to describing it"}
          </button>
        </div>
      )}
      <form
        className="ds-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="ds-grid">
          <label className="field" htmlFor={id("name")}>
            <span>Name</span>
            <input
              id={id("name")}
              data-autofocus
              value={form.name}
              maxLength={40}
              spellCheck={false}
              autoComplete="off"
              aria-invalid={invalid("name")}
              aria-describedby={describe("name") ?? id("name-hint")}
              onChange={(event) => update({ name: event.target.value })}
            />
            <span className="field-hint" id={id("name-hint")}>
              The agent sees its tools as <code>mcp__{form.name.trim() || "name"}__…</code>
            </span>
          </label>
          <label className="field" htmlFor={id("label")}>
            <span>Label (optional)</span>
            <input id={id("label")} value={form.label} onChange={(event) => update({ label: event.target.value })} />
          </label>

          <div className="field ds-span-2">
            <span id={id("transport")}>Connects through</span>
            <div className="seg" role="radiogroup" aria-labelledby={id("transport")}>
              {(["http", "stdio"] as const).map((transport) => (
                <button
                  key={transport}
                  type="button"
                  role="radio"
                  aria-checked={form.transport === transport}
                  tabIndex={form.transport === transport ? 0 : -1}
                  className={`seg-item${form.transport === transport ? " is-on" : ""}`}
                  onClick={() => update({ transport })}
                  onKeyDown={(event) => {
                    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
                      event.preventDefault();
                      const next = form.transport === "http" ? "stdio" : "http";
                      update({ transport: next });
                      (event.currentTarget.parentElement?.querySelector(`[data-transport="${next}"]`) as HTMLElement | null)?.focus();
                    }
                  }}
                  data-transport={transport}
                >
                  {transportLabel(transport)}
                </button>
              ))}
            </div>
          </div>

          {form.transport === "http" ? (
            <label className="field ds-span-2" htmlFor={id("url")}>
              <span>Web address</span>
              <input
                id={id("url")}
                value={form.url}
                placeholder="https://"
                spellCheck={false}
                autoComplete="off"
                aria-invalid={invalid("url")}
                aria-describedby={describe("url")}
                onChange={(event) => update({ url: event.target.value })}
              />
            </label>
          ) : (
            <>
              <label className="field ds-span-2" htmlFor={id("command")}>
                <span>Command</span>
                <input
                  id={id("command")}
                  value={form.command}
                  placeholder="npx"
                  spellCheck={false}
                  autoComplete="off"
                  aria-invalid={invalid("command")}
                  aria-describedby={describe("command")}
                  onChange={(event) => update({ command: event.target.value })}
                />
              </label>
              <label className="field ds-span-2" htmlFor={id("args")}>
                <span>Arguments, one per line</span>
                <textarea
                  id={id("args")}
                  className="field-textarea is-mono"
                  rows={3}
                  value={form.args}
                  spellCheck={false}
                  onChange={(event) => update({ args: event.target.value })}
                />
              </label>
            </>
          )}

          <fieldset className="field ds-span-2 secrets" aria-describedby={id("secrets-note")}>
            <legend>{secretsWord}</legend>
            {form.secrets.length === 0 && <p className="field-hint">None.</p>}
            {form.secrets.map((row, index) => (
              <Fragment key={row.key}>
              <div className="secret-row">
                <input
                  aria-label={`${secretsWord} name ${index + 1}`}
                  value={row.name}
                  placeholder={form.transport === "http" ? "Authorization" : "API_KEY"}
                  spellCheck={false}
                  autoComplete="off"
                  aria-invalid={invalid("secrets") && !row.name.trim() ? true : undefined}
                  onChange={(event) => updateRow(row.key, { name: event.target.value })}
                />
                <input
                  type="password"
                  aria-label={`${secretsWord} value ${index + 1}${row.stored ? " (a value is stored)" : ""}`}
                  value={row.value}
                  placeholder={row.stored ? "Stored · type to replace" : "Value"}
                  autoComplete="new-password"
                  aria-invalid={invalid("secrets") && !row.value ? true : undefined}
                  onChange={(event) => updateRow(row.key, { value: event.target.value })}
                />
                <button
                  type="button"
                  className="icon-btn"
                  aria-label={`Remove ${row.name || `row ${index + 1}`}`}
                  onClick={() => update({ secrets: form.secrets.filter((item) => item.key !== row.key) })}
                >
                  <Icon name="x" size={14} />
                </button>
              </div>
              {row.hint && <p className="field-hint secret-hint">{row.hint}</p>}
              </Fragment>
            ))}
            <button
              type="button"
              className="btn btn-small secret-add"
              onClick={() => update({ secrets: [...form.secrets, secretRow()] })}
            >
              <Icon name="plus" size={13} strokeWidth={2.2} /> Add {form.transport === "http" ? "a header" : "a variable"}
            </button>
            <span className="ds-keychain" id={id("secrets-note")}>
              <Icon name="shield" size={12} /> Values are stored in your Mac's Keychain, not in project files. They are never
              shown again, and the assistant never sees them.
            </span>
          </fieldset>

          <label className="field ds-span-2" htmlFor={id("description")}>
            <span>Description (optional)</span>
            <input
              id={id("description")}
              value={form.description}
              onChange={(event) => update({ description: event.target.value })}
            />
          </label>
        </div>

        {fieldError && (
          <p className="ds-field-error" id={id("error")} role="alert">
            {fieldError.message}
          </p>
        )}
        {error && (
          <p className="ds-error" role="alert">
            <Icon name="alert" size={14} /> {error}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" className="btn" disabled={saving} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving && <Spinner size={13} />}
            {server ? "Save" : "Add"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---- describe what you need (adding only) -----------------------------------------------

const SOURCE_WORDS: Record<string, string> = {
  yours: "Added in this studio",
  machine: "On this computer",
  plugin: "From a plugin",
  suggested: "Suggested by the studio",
  app: "From this app",
};

export function DescribeStep({
  text,
  problem,
  asking,
  error,
  answer,
  now,
  onText,
  onSuggest,
  onForm,
  onUse,
}: {
  text: string;
  problem: string | null;
  asking: boolean;
  error: string | null;
  answer: McpSuggestAnswer | null;
  now?: number;
  onText: (text: string) => void;
  onSuggest: () => void;
  onForm: () => void;
  onUse: (suggestion: McpSuggestion) => void;
}) {
  const ids = useId();
  const left = answer ? leftOutLine(answer.left_out) : null;
  return (
    <div className="suggest">
      <form
        className="ds-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSuggest();
        }}
      >
        <label className="field" htmlFor={`${ids}-text`}>
          <span>Describe what you need</span>
          <textarea
            id={`${ids}-text`}
            data-autofocus
            className="field-textarea"
            rows={3}
            value={text}
            maxLength={MAX_SUGGEST_TEXT}
            placeholder={`For example: ${SUGGEST_EXAMPLE}`}
            aria-invalid={problem ? true : undefined}
            disabled={asking}
            onChange={(event) => onText(event.target.value)}
          />
        </label>
        <p className="field-hint">
          Say what the agent should reach or do. A model suggests a server from what it remembers; the studio checks what
          it can, and nothing is added until you press Add.
        </p>
        {problem && (
          <p className="ds-field-error" role="alert">
            {problem}
          </p>
        )}
        {error && (
          <p className="ds-error" role="alert">
            <Icon name="alert" size={14} /> {error}
          </p>
        )}
        {asking && (
          <p className="dialog-body" role="status">
            <Spinner /> Asking the model… (up to 40 seconds)
          </p>
        )}
        <div className="dialog-actions suggest-actions">
          <button type="button" className="btn suggest-self" disabled={asking} onClick={onForm}>
            Fill in the form yourself
          </button>
          <button type="submit" className="btn btn-primary" disabled={asking}>
            {asking && <Spinner size={13} />}
            Suggest
          </button>
        </div>
      </form>

      {answer && !asking && (
        <div className="suggest-answer" aria-live="polite">
          {answer.note && <p className="ds-note">{answer.note}</p>}
          {answer.have.length > 0 && (
            <section className="suggest-part">
              <h3>Already in your library</h3>
              <ul className="suggest-list">
                {answer.have.map((item) => (
                  <li key={item.id} className="suggest-card is-have">
                    <span className="suggest-title">{item.label || item.name}</span>
                    <span className="suggest-where">{item.origin || SOURCE_WORDS[item.source] || item.source}</span>
                    {item.description && <p className="suggest-text">{item.description}</p>}
                    {connectorState(item.state, item.fix) ? (
                      <ConnectorStateNote state={item.state} fix={item.fix} />
                    ) : (
                      item.fix && <p className="field-hint">{item.fix}</p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {answer.suggestions.length > 0 && (
            <section className="suggest-part">
              <h3>{suggestedHeading(answer.asked)}</h3>
              <ul className="suggest-list">
                {answer.suggestions.map((suggestion) => (
                  <SuggestionCard key={suggestion.name} suggestion={suggestion} now={now} onUse={() => onUse(suggestion)} />
                ))}
              </ul>
            </section>
          )}
          {left && <p className="field-hint suggest-left">{left}</p>}
          {answerIsEmpty(answer) && (
            <div className="suggest-empty">
              <p>Nothing was suggested, and nothing in your library matches. You can fill in the form yourself.</p>
              <button type="button" className="btn" onClick={onForm}>
                Fill in the form yourself
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function SuggestionCard({
  suggestion,
  now,
  onUse,
}: {
  suggestion: McpSuggestion;
  now?: number;
  onUse: () => void;
}) {
  const caution = checkCaution(suggestion.check, now);
  const keysWord = suggestion.transport === "stdio" ? "Values it needs" : "Keys it needs";
  return (
    <li className="suggest-card">
      <span className="suggest-title">
        {suggestion.label || suggestion.name}
        {suggestion.label && suggestion.label !== suggestion.name && <code className="mcp-name">{suggestion.name}</code>}
      </span>
      {suggestion.description && <p className="suggest-text">{suggestion.description}</p>}
      {suggestion.why && (
        <p className="suggest-text suggest-why">
          <strong>Why it fits:</strong> {suggestion.why}
        </p>
      )}
      <code className="suggest-code">{suggestionTarget(suggestion) || "—"}</code>
      <p className="suggest-check">{checkLine(suggestion.check, now)}</p>
      {caution && (
        <p className="suggest-caution">
          <Icon name="warning" size={14} /> {caution}
        </p>
      )}
      {suggestion.secrets.length > 0 && (
        <div className="suggest-keys">
          <span className="suggest-keys-title">{keysWord}</span>
          <ul>
            {suggestion.secrets.map((secret) => (
              <li key={secret.name}>
                <code>{secret.name}</code>
                {secret.where && <span>: {secret.where}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {suggestion.sign_in ? (
        <div className="suggest-signin">
          <p className="suggest-text">{SIGN_IN_NOTE}</p>
          {signInCommands(suggestion).map((command) => (
            <code key={command} className="suggest-code">
              {command}
            </code>
          ))}
        </div>
      ) : (
        <div className="suggest-use">
          <button type="button" className="btn btn-small btn-primary" onClick={onUse}>
            Use this
          </button>
        </div>
      )}
    </li>
  );
}

// ---- the list --------------------------------------------------------------------------

export function McpServersView({
  servers,
  query,
  onChanged,
  manage = true,
  startAdding = false,
  sources,
  title = "Connectors",
  secondary = "MCP servers",
  intro = "Extra tools from other programs or web services. Switch them on per project.",
  headingId = "lib-mcp",
  exclude,
  level = 3,
}: {
  servers: McpServer[];
  query: string;
  onChanged: () => void;
  /** Add, edit and delete; testing is always available. */
  manage?: boolean;
  /** Open the Add dialog at "Describe what you need" once (`/library#new-connector`). */
  startAdding?: boolean;
  /** Only these groups (by source), e.g. the person's own and the suggested ones. All when left out. */
  sources?: readonly string[] | null;
  /** Leave these groups out (by source). */
  exclude?: readonly string[];
  /** The heading's level: 2 where the connectors are a part of the Library of their own. */
  level?: 2 | 3;
  title?: string;
  /** The builder's word, shown smaller after the title. */
  secondary?: string | null;
  intro?: string | null;
  headingId?: string;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<McpServer | "new" | null>(startAdding && manage ? "new" : null);
  const [tests, setTests] = useState<Record<string, { busy: boolean; result: McpTestResult | null; at: string | null; error: string | null }>>({});
  const [now] = useState(() => Date.now());

  const runTest = async (server: McpServer) => {
    setTests((map) => ({ ...map, [server.id]: { busy: true, result: null, at: null, error: null } }));
    try {
      const result = await api.testMcpServer(server.id);
      setTests((map) => ({ ...map, [server.id]: { busy: false, result, at: new Date().toISOString(), error: null } }));
    } catch (cause) {
      setTests((map) => ({ ...map, [server.id]: { busy: false, result: null, at: null, error: errorMessage(cause) } }));
    }
  };

  const remove = async (server: McpServer) => {
    const ok = await confirm({
      title: `Delete “${server.label || server.name}”?`,
      body: "It is switched off in every project that uses it, and its stored secrets are deleted from the Keychain.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.deleteMcpServer(server.id);
      toast({ message: `Deleted “${server.label || server.name}”` });
      onChanged();
    } catch (cause) {
      toast({ message: errorMessage(cause), kind: "error" });
    }
  };

  const Heading = level === 2 ? "h2" : "h3";
  const groups = groupMcp(servers)
    .filter((group) => (!sources || sources.includes(group.source)) && !exclude?.includes(group.source))
    .map((group) => ({
    ...group,
    shown: query ? group.items.filter((server) => serverMatches(server, query)) : group.items,
  }));
  if (query && groups.every((group) => group.shown.length === 0)) return null;

  return (
    <section className="lib-block-mcp" aria-labelledby={headingId}>
      <Heading id={headingId} className={level === 2 ? "lib-group-title" : "lib-title"}>
        {title}
        {secondary && <span className="lib-title-aside"> ({secondary})</span>}{" "}
        {servers.length > 0 && <span className="lib-count">{servers.length}</span>}
      </Heading>
      {intro && <p className="lib-intro">{intro}</p>}
      {groups.map((group) => {
        if (query && group.shown.length === 0) return null;
        return (
          <div key={group.source} className="mcp-group">
            <div className="mcp-group-head">
              <h4>{group.title}</h4>
              {manage && group.source === "yours" && (
                <button type="button" className="btn btn-small" onClick={() => setEditing("new")}>
                  <Icon name="plus" size={13} strokeWidth={2.2} /> Add a connector
                </button>
              )}
            </div>
            {group.intro && <p className="lib-intro">{group.intro}</p>}
            {group.shown.length === 0 ? (
              <p className="lib-empty">{group.empty}</p>
            ) : (
              <ul className="lib-rows">
                {group.shown.map((server) => {
                  const test = tests[server.id];
                  const display = test?.result ?? server.last_test;
                  const at = test?.result ? test.at : (server.last_test?.at ?? null);
                  const title = server.label || server.name;
                  return (
                    <li key={server.id} className="lib-row mcp-row">
                      <div className="lib-row-main">
                        <span className="lib-row-title">
                          {title}
                          {server.label && server.label !== server.name && <code className="mcp-name">{server.name}</code>}
                        </span>
                        {server.description && <span className="lib-row-detail">{server.description}</span>}
                        <span className="mcp-meta">
                          <span className="lib-status is-neutral">{transportLabel(server.transport)}</span>
                          <code className="mcp-target" title={mcpTarget(server)}>
                            {mcpTarget(server) || "—"}
                          </code>
                        </span>
                        {server.secret_names?.length > 0 && (
                          <span className="lib-row-detail">
                            {server.transport === "http" ? "Headers" : "Environment"}: {server.secret_names.join(", ")} (values
                            stored, not shown)
                          </span>
                        )}
                        <TestLine result={display} at={at} now={now} />
                        {test?.error && (
                          <p className="ds-error" role="alert">
                            {test.error}
                          </p>
                        )}
                      </div>
                      <div className="ds-item-actions">
                        <button
                          type="button"
                          className="btn btn-small"
                          disabled={test?.busy}
                          aria-label={`Test ${title}`}
                          onClick={() => void runTest(server)}
                        >
                          {test?.busy && <Spinner size={12} />}
                          {test?.busy ? "Testing…" : "Test"}
                        </button>
                        {manage && server.source === "yours" && (
                          <>
                            <button
                              type="button"
                              className="btn btn-small"
                              aria-label={`Edit ${title}`}
                              onClick={() => setEditing(server)}
                            >
                              Edit
                            </button>
                            <button
                              type="button"
                              className="icon-btn"
                              aria-label={`Delete ${title}`}
                              data-tip="Delete"
                              data-tip-align="end"
                              onClick={() => void remove(server)}
                            >
                              <Icon name="trash" size={15} />
                            </button>
                          </>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        );
      })}
      {editing && (
        <McpServerDialog
          server={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            toast({ message: editing === "new" ? `Added “${saved.label || saved.name}”` : `Saved “${saved.label || saved.name}”`, kind: "success" });
            onChanged();
          }}
        />
      )}
    </section>
  );
}
