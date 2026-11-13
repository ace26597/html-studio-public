// The data source form: pure rules for kind defaults, validation and request bodies.

import type { DataKindInfo, DataSource, DataSourceInput } from "./types";

export const SSL_MODES = ["disable", "prefer", "require", "verify-full"] as const;

const FALLBACK_PORTS: Record<string, number> = { postgres: 5432, redshift: 5439, mysql: 3306 };

/**
 * What happens to the stored password on save:
 * - `none`: there is none and none was typed (create without password),
 * - `keep`: leave the stored one alone (edit),
 * - `set`: send the typed one (create, or replace on edit),
 * - `remove`: send `password: ""` (edit).
 */
export type PasswordAction = "keep" | "set" | "remove" | "none";

export interface DataSourceForm {
  name: string;
  kind: string;
  host: string;
  port: string;
  database: string;
  user: string;
  ssl: string;
  password: string;
  passwordAction: PasswordAction;
}

export function defaultSsl(kind: string): string {
  return kind === "redshift" ? "require" : "prefer";
}

export function defaultPort(kind: string, kinds: readonly DataKindInfo[]): number | null {
  return kinds.find((item) => item.kind === kind)?.default_port ?? FALLBACK_PORTS[kind] ?? null;
}

export function emptyForm(kinds: readonly DataKindInfo[]): DataSourceForm {
  const kind = kinds.find((item) => item.available)?.kind ?? kinds[0]?.kind ?? "postgres";
  const port = defaultPort(kind, kinds);
  return {
    name: "",
    kind,
    host: "",
    port: port === null ? "" : String(port),
    database: "",
    user: "",
    ssl: defaultSsl(kind),
    password: "",
    passwordAction: "none",
  };
}

export function formFromSource(source: DataSource): DataSourceForm {
  return {
    name: source.name,
    kind: source.kind,
    host: source.host,
    port: String(source.port ?? ""),
    database: source.database,
    user: source.user,
    ssl: source.ssl,
    password: "",
    passwordAction: source.has_password ? "keep" : "none",
  };
}

/** Changing the kind moves the port and SSL to the new defaults, unless the person had changed them. */
export function changeKind(form: DataSourceForm, kind: string, kinds: readonly DataKindInfo[]): DataSourceForm {
  const oldPort = defaultPort(form.kind, kinds);
  const newPort = defaultPort(kind, kinds);
  const portUntouched = form.port.trim() === "" || (oldPort !== null && form.port.trim() === String(oldPort));
  const sslUntouched = form.ssl === defaultSsl(form.kind);
  return {
    ...form,
    kind,
    port: portUntouched ? (newPort === null ? "" : String(newPort)) : form.port,
    ssl: sslUntouched ? defaultSsl(kind) : form.ssl,
  };
}

export interface FieldError {
  field: "name" | "host" | "port" | "database" | "user" | "password";
  message: string;
}

export function validateForm(form: DataSourceForm): FieldError | null {
  const name = form.name.trim();
  if (!name) return { field: "name", message: "Give it a name." };
  if (name.length > 60) return { field: "name", message: "The name can be at most 60 characters." };
  if (!form.host.trim()) return { field: "host", message: "Enter the host name or address." };
  const port = form.port.trim();
  if (port && !(/^\d+$/.test(port) && Number(port) >= 1 && Number(port) <= 65535)) {
    return { field: "port", message: "The port is a number from 1 to 65535." };
  }
  if (!form.database.trim()) return { field: "database", message: "Enter the database name." };
  if (!form.user.trim()) return { field: "user", message: "Enter the user name." };
  if (form.passwordAction === "set" && !form.password) {
    return { field: "password", message: "Type the new password, or keep the stored one." };
  }
  return null;
}

/** The full body for create and for testing before saving. The password goes only when typed. */
export function inputFromForm(form: DataSourceForm): DataSourceInput {
  const body: DataSourceInput = {
    name: form.name.trim(),
    kind: form.kind,
    host: form.host.trim(),
    database: form.database.trim(),
    user: form.user.trim(),
    ssl: form.ssl,
  };
  if (form.port.trim()) body.port = Number(form.port.trim());
  if (form.passwordAction === "set" && form.password) body.password = form.password;
  return body;
}

/** Only what changed, for PATCH. `password: ""` removes the stored password. */
export function patchFromForm(form: DataSourceForm, original: DataSource): Partial<DataSourceInput> {
  const full = inputFromForm(form);
  const patch: Partial<DataSourceInput> = {};
  if (full.name !== original.name) patch.name = full.name;
  if (full.kind !== original.kind) patch.kind = full.kind;
  if (full.host !== original.host) patch.host = full.host;
  if (full.port !== undefined && full.port !== original.port) patch.port = full.port;
  if (full.database !== original.database) patch.database = full.database;
  if (full.user !== original.user) patch.user = full.user;
  if (full.ssl !== original.ssl) patch.ssl = full.ssl;
  if (form.passwordAction === "set" && form.password) patch.password = form.password;
  if (form.passwordAction === "remove" && original.has_password) patch.password = "";
  return patch;
}

/**
 * How "Test connection" should run from the dialog:
 * - `saved`: an existing source with no unsaved changes, tested with its stored password;
 * - `draft`: the form as typed, without saving;
 * - `draft-without-stored-password`: the form changed but the stored password cannot be
 *   sent back from the browser, so the draft is tested without one (say so).
 */
export function testMode(
  form: DataSourceForm,
  original: DataSource | null,
): "saved" | "draft" | "draft-without-stored-password" {
  if (!original) return "draft";
  const patch = patchFromForm(form, original);
  if (!Object.keys(patch).length) return "saved";
  if (original.has_password && form.passwordAction === "keep") return "draft-without-stored-password";
  return "draft";
}
