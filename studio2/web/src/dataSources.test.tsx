import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SslChoice } from "./DataSources";
import { SSL_HELP, SSL_MODES } from "./dataSourceForm";

const draw = (ssl: string, caFile = "", kind = "postgres") =>
  renderToStaticMarkup(<SslChoice id="t" kind={kind} ssl={ssl} caFile={caFile} onChange={() => {}} />);

describe("the encryption choice", () => {
  it("shows each mode with its sentence next to it", () => {
    const html = draw("prefer");
    for (const mode of SSL_MODES) {
      expect(html).toContain(`value="${mode}"`);
      expect(html).toContain(SSL_HELP[mode].replace(/’/g, "’"));
    }
    expect(html.match(/type="radio"/g)).toHaveLength(4);
    const checked = (html.match(/<input[^>]*>/g) ?? []).filter((tag) => tag.includes("checked"));
    expect(checked).toHaveLength(1);
    expect(checked[0]).toContain(`value="prefer"`);
  });

  it("warns under MySQL in every mode but verify-full, and never for PostgreSQL", () => {
    expect(draw("require", "", "mysql")).toContain("only verify-full keeps the password");
    expect(draw("verify-full", "", "mysql")).not.toContain("only verify-full keeps the password");
    expect(draw("require")).not.toContain("only verify-full keeps the password");
  });

  it("asks for a CA file only under verify-full", () => {
    expect(draw("require")).not.toContain("CA file");
    const html = draw("verify-full", "/etc/ssl/db-ca.pem");
    expect(html).toContain("CA file");
    expect(html).toContain('value="/etc/ssl/db-ca.pem"');
  });
});
