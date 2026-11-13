// MCP servers in the Library: your own, the ones on this Mac, and the studio's suggestions.
// Each can be tested; yours can be edited and deleted. Secret values are write-only.

import { useId, useState } from "react";
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
import type { McpServer, McpTestResult } from "./types";
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

  const secretsWord = form.transport === "http" ? "Headers" : "Environment";
  const invalid = (field: McpFieldError["field"]) => (fieldError?.field === field ? true : undefined);
  const describe = (field: McpFieldError["field"]) => (fieldError?.field === field ? id("error") : undefined);

  return (
    <Modal title={server ? `Edit ${server.label || server.name}` : "Add an MCP server"} onClose={onClose} busy={saving} wide>
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
              <div key={row.key} className="secret-row">
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

// ---- the list --------------------------------------------------------------------------

export function McpServersView({
  servers,
  query,
  onChanged,
  manage = true,
}: {
  servers: McpServer[];
  query: string;
  onChanged: () => void;
  /** Add, edit and delete; testing is always available. */
  manage?: boolean;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState<McpServer | "new" | null>(null);
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

  const groups = groupMcp(servers).map((group) => ({
    ...group,
    shown: query ? group.items.filter((server) => serverMatches(server, query)) : group.items,
  }));
  if (query && groups.every((group) => group.shown.length === 0)) return null;

  return (
    <section className="lib-block-mcp" aria-labelledby="lib-mcp">
      <h3 id="lib-mcp" className="lib-title">
        MCP servers {servers.length > 0 && <span className="lib-count">{servers.length}</span>}
      </h3>
      <p className="lib-intro">Extra tools from other programs or web services. Switch them on per project.</p>
      {groups.map((group) => {
        if (query && group.shown.length === 0) return null;
        return (
          <div key={group.source} className="mcp-group">
            <div className="mcp-group-head">
              <h4>{group.title}</h4>
              {manage && group.source === "yours" && (
                <button type="button" className="btn btn-small" onClick={() => setEditing("new")}>
                  <Icon name="plus" size={13} strokeWidth={2.2} /> Add a server
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

// ---- skills you add ----------------------------------------------------------------------

export function SkillDialog({ onSaved, onClose }: { onSaved: (name: string) => void; onClose: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const ids = useId();

  const save = async () => {
    const trimmed = name.trim();
    const issue =
      (trimmed ? null : "Give it a name.") ??
      (trimmed.length > 60 ? "The name can be at most 60 characters." : null) ??
      (/^[a-z0-9_-]+$/.test(trimmed) ? null : "Use lower-case letters, digits, “-” and “_” only.") ??
      (description.trim() ? null : "Say in one line when the agent should use it.") ??
      (body.trim() ? null : "Write the instructions.");
    if (issue) {
      setProblem(issue);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.createSkill({ name: trimmed, description: description.trim(), body });
      onSaved(trimmed);
    } catch (cause) {
      setError(errorMessage(cause));
      setSaving(false);
    }
  };

  return (
    <Modal title="Add a skill" onClose={onClose} busy={saving} wide>
      <form
        className="ds-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <label className="field" htmlFor={`${ids}-name`}>
          <span>Name</span>
          <input
            id={`${ids}-name`}
            data-autofocus
            value={name}
            maxLength={60}
            spellCheck={false}
            autoComplete="off"
            placeholder="weekly-report"
            onChange={(event) => {
              setName(event.target.value);
              setProblem(null);
            }}
          />
        </label>
        <label className="field" htmlFor={`${ids}-description`}>
          <span>When to use it</span>
          <input
            id={`${ids}-description`}
            value={description}
            placeholder="Use when the person asks for the weekly sales report."
            onChange={(event) => {
              setDescription(event.target.value);
              setProblem(null);
            }}
          />
        </label>
        <label className="field" htmlFor={`${ids}-body`}>
          <span>Instructions (Markdown)</span>
          <textarea
            id={`${ids}-body`}
            className="field-textarea is-mono"
            rows={10}
            value={body}
            onChange={(event) => {
              setBody(event.target.value);
              setProblem(null);
            }}
          />
        </label>
        <p className="field-hint">
          Saved in the studio, not in a project. On in every project unless you switch it off there.
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
        <div className="dialog-actions">
          <button type="button" className="btn" disabled={saving} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving && <Spinner size={13} />}
            Add
          </button>
        </div>
      </form>
    </Modal>
  );
}
