import { describe, expect, it } from "vitest";
import {
  changeKind,
  defaultSsl,
  emptyForm,
  formFromSource,
  inputFromForm,
  needsPasswordAgain,
  patchFromForm,
  SSL_HELP,
  sslWarning,
  SSL_MODES,
  testMode,
  validateForm,
} from "./dataSourceForm";
import type { DataKindInfo, DataSource } from "./types";

const kinds: DataKindInfo[] = [
  { kind: "postgres", label: "PostgreSQL", default_port: 5432, available: true, note: null },
  { kind: "redshift", label: "Redshift", default_port: 5439, available: true, note: null },
  { kind: "mysql", label: "MySQL", default_port: 3306, available: false, note: null },
];

const saved: DataSource = {
  id: "ds_1",
  name: "W",
  kind: "postgres",
  host: "h",
  port: 5432,
  database: "d",
  user: "u",
  ssl: "prefer",
  has_password: true,
  created_at: "2026-11-10T00:00:00Z",
  last_test: null,
};

const filled = () => ({ ...emptyForm(kinds), name: "W", host: "h", database: "d", user: "u" });

describe("data source form", () => {
  it("starts on the first available kind with its defaults", () => {
    expect(emptyForm(kinds)).toMatchObject({ kind: "postgres", port: "5432", ssl: "prefer", passwordAction: "none" });
  });

  it("moves port and SSL with the kind unless they were changed", () => {
    const red = changeKind(emptyForm(kinds), "redshift", kinds);
    expect(red).toMatchObject({ port: "5439", ssl: "require" });
    const custom = changeKind({ ...emptyForm(kinds), port: "6000", ssl: "disable" }, "redshift", kinds);
    expect(custom).toMatchObject({ port: "6000", ssl: "disable" });
    expect(defaultSsl("mysql")).toBe("prefer");
  });

  it("validates the fields the server requires", () => {
    expect(validateForm(emptyForm(kinds))?.field).toBe("name");
    expect(validateForm({ ...filled(), name: "x".repeat(61) })?.field).toBe("name");
    expect(validateForm({ ...filled(), port: "70000" })?.field).toBe("port");
    expect(validateForm({ ...filled(), port: "" })).toBeNull();
    expect(validateForm({ ...filled(), passwordAction: "set", password: "" })?.field).toBe("password");
    expect(validateForm(filled())).toBeNull();
  });

  it("sends the password only when typed", () => {
    expect(inputFromForm(filled())).not.toHaveProperty("password");
    expect(inputFromForm({ ...filled(), password: "pw", passwordAction: "set" }).password).toBe("pw");
    expect(inputFromForm({ ...filled(), port: "5433" }).port).toBe(5433);
  });

  it("never echoes a stored password into the form", () => {
    const form = formFromSource(saved);
    expect(form.password).toBe("");
    expect(form.passwordAction).toBe("keep");
  });

  it("patches only what changed; keep sends no password, remove sends an empty one, replace sends the new one", () => {
    const form = formFromSource(saved);
    expect(patchFromForm(form, saved)).toEqual({});
    expect(patchFromForm({ ...form, host: "h2" }, saved)).toEqual({ host: "h2" });
    expect(patchFromForm({ ...form, passwordAction: "remove" }, saved)).toEqual({ password: "" });
    expect(patchFromForm({ ...form, passwordAction: "set", password: "new" }, saved)).toEqual({ password: "new" });
  });

  it("chooses how to test", () => {
    expect(testMode(filled(), null)).toBe("draft");
    const form = formFromSource(saved);
    expect(testMode(form, saved)).toBe("saved");
    expect(testMode({ ...form, host: "other" }, saved)).toBe("draft-without-stored-password");
    expect(testMode({ ...form, host: "other", passwordAction: "set", password: "x" }, saved)).toBe("draft");
    expect(testMode({ ...formFromSource({ ...saved, has_password: false }), host: "o" }, { ...saved, has_password: false })).toBe("draft");
  });

  it("explains every TLS mode in one sentence, saying which ones do not check the server", () => {
    for (const mode of SSL_MODES) expect(SSL_HELP[mode].length).toBeGreaterThan(20);
    expect(SSL_HELP.disable).toMatch(/never encrypted/i);
    expect(SSL_HELP.prefer).toMatch(/does not check who the server is/);
    expect(SSL_HELP.require).toMatch(/does not check who the server is/);
    expect(SSL_HELP["verify-full"]).toMatch(/proves/);
    // MySQL's standard login can hand the password to a server that pretends, unless verify-full.
    for (const mode of ["disable", "prefer", "require"]) expect(sslWarning("mysql", mode)).toMatch(/only verify-full/);
    expect(sslWarning("mysql", "verify-full")).toBeNull();
    for (const mode of SSL_MODES) expect(sslWarning("postgres", mode)).toBeNull();
  });

  it("sends the CA file only with verify-full, and an emptied one as a removal", () => {
    const form = { ...filled(), ssl: "verify-full", caFile: " /etc/ssl/db-ca.pem " };
    expect(emptyForm(kinds).caFile).toBe("");
    expect(inputFromForm(form).ca_file).toBe("/etc/ssl/db-ca.pem");
    expect(inputFromForm({ ...form, ssl: "require" })).not.toHaveProperty("ca_file");
    const withFile = { ...saved, ssl: "verify-full", ca_file: "/etc/ssl/db-ca.pem" };
    const edit = formFromSource(withFile);
    expect(edit.caFile).toBe("/etc/ssl/db-ca.pem");
    expect(patchFromForm(edit, withFile)).toEqual({});
    expect(patchFromForm({ ...edit, caFile: "" }, withFile)).toEqual({ ca_file: "" });
    expect(patchFromForm({ ...edit, caFile: "/other.pem" }, withFile)).toEqual({ ca_file: "/other.pem" });
    expect(patchFromForm({ ...edit, ssl: "require" }, withFile)).toEqual({ ssl: "require" });   // kept, unused
    expect(testMode({ ...edit, caFile: "/other.pem" }, withFile)).toBe("draft-without-stored-password");
  });

  it("asks for a full path for the CA file", () => {
    expect(validateForm({ ...filled(), ssl: "verify-full", caFile: "certs/ca.pem" })?.field).toBe("caFile");
    expect(validateForm({ ...filled(), ssl: "verify-full", caFile: "~/certs/ca.pem" })).toBeNull();
    expect(validateForm({ ...filled(), ssl: "require", caFile: "certs/ca.pem" })).toBeNull();
  });
});

describe("a CA file that changed since the password was saved", () => {
  const refused = { ok: false, message: "The CA file /x has changed", code: "ca_changed" };
  const changed: DataSource = {
    ...saved,
    ssl: "verify-full",
    ca_file: "/x",
    last_test: { ...refused, at: "2026-11-17T00:00:00Z" },
  };

  it("is recognised from a fresh test or from the last one, only with a stored password", () => {
    expect(needsPasswordAgain(changed)).toBe(true);
    expect(needsPasswordAgain(saved, refused)).toBe(true);
    expect(needsPasswordAgain(saved)).toBe(false);
    expect(needsPasswordAgain(changed, { ok: true, message: "Connected" })).toBe(false); // a newer test wins
    expect(needsPasswordAgain({ ...changed, has_password: false })).toBe(false);
  });

  it("opens the form ready to type the password, and saving sends only the password", () => {
    const form = formFromSource(changed, { retypePassword: true });
    expect(form.passwordAction).toBe("set");
    expect(validateForm(form)?.field).toBe("password");
    expect(patchFromForm({ ...form, password: "pw" }, changed)).toEqual({ password: "pw" });
  });
});
