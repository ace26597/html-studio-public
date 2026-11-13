import { describe, expect, it } from "vitest";
import {
  emptyMcpForm,
  mcpFormFromServer,
  mcpInputFromForm,
  mcpPatchFromForm,
  parseArgs,
  secretRow,
  validateMcpForm,
  type McpForm,
} from "./mcpServerForm";
import type { McpServer } from "./types";

const saved: McpServer = {
  id: "mcp_1",
  name: "docs",
  label: "Docs",
  transport: "http",
  url: "https://example.test/mcp",
  command: null,
  args: [],
  secret_names: ["Authorization"],
  source: "yours",
  description: null,
  last_test: null,
};

const http = (patch: Partial<McpForm> = {}): McpForm => ({ ...emptyMcpForm(), name: "docs", url: "https://example.test/mcp", ...patch });

describe("MCP server form", () => {
  it("validates name, address and command", () => {
    expect(validateMcpForm(http({ name: "" }))?.field).toBe("name");
    expect(validateMcpForm(http({ name: "studio" }))?.field).toBe("name");
    expect(validateMcpForm(http({ url: "example.test" }))?.field).toBe("url");
    expect(validateMcpForm(http({ transport: "stdio", command: "" }))?.field).toBe("command");
    expect(validateMcpForm(http())).toBeNull();
  });

  it("needs a name and a value for each new secret, and unique names", () => {
    const row = { ...secretRow("Authorization"), value: "" };
    expect(validateMcpForm(http({ secrets: [row] }))?.field).toBe("secrets");
    expect(validateMcpForm(http({ secrets: [{ ...secretRow(""), value: "x" }] }))?.message).toBe("Every value needs a name.");
    const a = { ...secretRow("A"), value: "1" };
    expect(validateMcpForm(http({ secrets: [a, { ...secretRow("A"), value: "2" }] }))?.message).toContain("once");
    expect(validateMcpForm(http({ secrets: [a] }))).toBeNull();
    // An empty row is ignored.
    expect(validateMcpForm(http({ secrets: [secretRow()] }))).toBeNull();
  });

  it("builds a create body: headers for a web address, command, args and env for a command", () => {
    expect(mcpInputFromForm(http({ secrets: [{ ...secretRow("Authorization"), value: "Bearer x" }] }))).toEqual({
      name: "docs",
      transport: "http",
      url: "https://example.test/mcp",
      headers: { Authorization: "Bearer x" },
    });
    const body = mcpInputFromForm(
      http({ transport: "stdio", command: "npx", args: " -y \n\n pkg@1 ", secrets: [{ ...secretRow("KEY"), value: "v" }] }),
    );
    expect(body).toEqual({ name: "docs", transport: "stdio", command: "npx", args: ["-y", "pkg@1"], env: { KEY: "v" } });
    expect(parseArgs("a\n b \n")).toEqual(["a", "b"]);
  });

  it("never fills a stored secret value into the form", () => {
    const form = mcpFormFromServer(saved);
    expect(form.secrets).toHaveLength(1);
    expect(form.secrets[0]).toMatchObject({ name: "Authorization", value: "", stored: true });
  });

  it("patches only what changed and leaves stored secrets alone when untouched", () => {
    const form = mcpFormFromServer(saved);
    expect(validateMcpForm(form, saved)).toBeNull();
    expect(mcpPatchFromForm(form, saved)).toEqual({});
    expect(mcpPatchFromForm({ ...form, label: "Docs 2" }, saved)).toEqual({ label: "Docs 2" });
  });

  it("sends the whole set of secrets when they change, and asks for every value", () => {
    const form = mcpFormFromServer(saved);
    const withNew = { ...form, secrets: [...form.secrets, { ...secretRow("X-Extra"), value: "e" }] };
    expect(validateMcpForm(withNew, saved)?.message).toContain("Authorization");
    const retyped = { ...withNew, secrets: withNew.secrets.map((row) => ({ ...row, value: row.value || "Bearer y" })) };
    expect(validateMcpForm(retyped, saved)).toBeNull();
    expect(mcpPatchFromForm(retyped, saved)).toEqual({ headers: { Authorization: "Bearer y", "X-Extra": "e" } });
    // Removing every row removes the stored secrets.
    expect(mcpPatchFromForm({ ...form, secrets: [] }, saved)).toEqual({ headers: {} });
  });

  it("asks for the values again when the transport changes", () => {
    const form = { ...mcpFormFromServer(saved), transport: "stdio" as const, command: "npx" };
    expect(validateMcpForm(form, saved)?.field).toBe("secrets");
  });
});
