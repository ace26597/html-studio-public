import { describe, expect, it } from "vitest";
import {
  changeKind,
  defaultSsl,
  emptyForm,
  formFromSource,
  inputFromForm,
  patchFromForm,
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
});
