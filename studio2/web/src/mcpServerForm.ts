// The MCP server form: pure rules for validation and request bodies.
// Secret values (headers for a web address, environment for a command) are
// write-only: the browser can send new ones but never reads stored ones.

import { nameProblem } from "./library";
import type { McpServer, McpServerInput } from "./types";

export interface SecretRow {
  key: number;
  name: string;
  value: string;
  /** The server already holds a value for this name (editing); `value` is empty unless retyped. */
  stored: boolean;
}

export interface McpForm {
  name: string;
  label: string;
  transport: "http" | "stdio";
  url: string;
  command: string;
  /** One argument per line, so no shell quoting is needed. */
  args: string;
  secrets: SecretRow[];
  description: string;
}

let rowKey = 0;
export function secretRow(name = "", stored = false): SecretRow {
  return { key: ++rowKey, name, value: "", stored };
}

export function emptyMcpForm(): McpForm {
  return { name: "", label: "", transport: "http", url: "", command: "", args: "", secrets: [], description: "" };
}

export function mcpFormFromServer(server: McpServer): McpForm {
  return {
    name: server.name,
    label: server.label ?? "",
    transport: server.transport === "stdio" ? "stdio" : "http",
    url: server.url ?? "",
    command: server.command ?? "",
    args: (server.args ?? []).join("\n"),
    secrets: (server.secret_names ?? []).map((name) => secretRow(name, true)),
    description: server.description ?? "",
  };
}

export function parseArgs(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export interface McpFieldError {
  field: "name" | "url" | "command" | "secrets";
  message: string;
}

/** True when the secret rows differ from what the server holds (names added, removed, renamed or a value typed). */
export function secretsChanged(form: McpForm, original: McpServer | null): boolean {
  const rows = form.secrets.filter((row) => row.name.trim() || row.value);
  if (!original) return rows.length > 0;
  const names = rows.map((row) => row.name.trim());
  const before = original.secret_names ?? [];
  if (names.length !== before.length || names.some((name, index) => name !== before[index])) return true;
  return rows.some((row) => row.value !== "");
}

/** Secrets go to the server when they changed, or when the transport changed (headers ⇄ environment). */
export function secretsMustBeSent(form: McpForm, original: McpServer | null): boolean {
  if (secretsChanged(form, original)) return true;
  const rows = form.secrets.filter((row) => row.name.trim() || row.value);
  return Boolean(original && rows.length && form.transport !== original.transport);
}

export function validateMcpForm(form: McpForm, original: McpServer | null = null): McpFieldError | null {
  const nameIssue = nameProblem(form.name.trim(), 40, "server");
  if (nameIssue) return { field: "name", message: nameIssue };
  if (form.transport === "http") {
    const url = form.url.trim();
    if (!/^https?:\/\/\S+$/i.test(url)) return { field: "url", message: "Enter the server’s web address, starting with https://." };
  } else if (!form.command.trim()) {
    return { field: "command", message: "Enter the command that starts the server." };
  }
  const rows = form.secrets.filter((row) => row.name.trim() || row.value);
  const names = rows.map((row) => row.name.trim());
  if (names.some((name) => !name)) return { field: "secrets", message: "Every value needs a name." };
  if (new Set(names).size !== names.length) return { field: "secrets", message: "Each name can be used once." };
  if (secretsMustBeSent(form, original)) {
    // The whole set is sent again, and stored values can't be read back.
    const missing = rows.find((row) => !row.value);
    if (missing) {
      return {
        field: "secrets",
        message: original
          ? `To change the ${form.transport === "http" ? "headers" : "environment"}, type every value again (including “${missing.name.trim()}”), or remove the rows you don’t need.`
          : `Type a value for “${missing.name.trim()}”, or remove that row.`,
      };
    }
  }
  return null;
}

function secretsMap(form: McpForm): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of form.secrets) if (row.name.trim()) out[row.name.trim()] = row.value;
  return out;
}

export function mcpInputFromForm(form: McpForm): McpServerInput {
  const body: McpServerInput = { name: form.name.trim(), transport: form.transport };
  if (form.label.trim()) body.label = form.label.trim();
  if (form.description.trim()) body.description = form.description.trim();
  if (form.transport === "http") {
    body.url = form.url.trim();
    const headers = secretsMap(form);
    if (Object.keys(headers).length) body.headers = headers;
  } else {
    body.command = form.command.trim();
    body.args = parseArgs(form.args);
    const env = secretsMap(form);
    if (Object.keys(env).length) body.env = env;
  }
  return body;
}

/**
 * Only what changed. Secrets are sent as a whole set (`headers` or `env`) when they
 * changed; an empty set removes them. Untouched secrets are not sent.
 */
export function mcpPatchFromForm(form: McpForm, original: McpServer): Partial<McpServerInput> {
  const full = mcpInputFromForm(form);
  const patch: Partial<McpServerInput> = {};
  if (full.name !== original.name) patch.name = full.name;
  if ((full.label ?? "") !== (original.label ?? "")) patch.label = full.label ?? "";
  if ((full.description ?? "") !== (original.description ?? "")) patch.description = full.description ?? "";
  if (full.transport !== original.transport) patch.transport = full.transport;
  if (full.transport === "http") {
    if (full.url !== (original.url ?? "") || patch.transport) patch.url = full.url;
  } else {
    if (full.command !== (original.command ?? "") || patch.transport) patch.command = full.command;
    const args = full.args ?? [];
    if (patch.transport || args.length !== original.args.length || args.some((arg, index) => arg !== original.args[index])) {
      patch.args = args;
    }
  }
  if (secretsMustBeSent(form, original)) {
    const map = secretsMap(form);
    if (form.transport === "http") patch.headers = map;
    else patch.env = map;
  }
  return patch;
}
