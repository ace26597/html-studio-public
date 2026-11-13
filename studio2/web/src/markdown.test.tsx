import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Markdown, safeHref } from "./markdown";

const html = (source: string) => renderToStaticMarkup(<Markdown text={source} />);

describe("escaping (model text is never trusted)", () => {
  it("shows raw HTML as text", () => {
    const out = html('Hello <script>alert("x")</script> <img src=x onerror=alert(1)>');
    expect(out).not.toContain("<script");
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;");
    expect(out).toContain("&lt;img src=x onerror=alert(1)&gt;");
  });

  it("escapes HTML inside every construct", () => {
    const out = html(
      [
        "# <b>heading</b>",
        "",
        "- <i>item</i>",
        "",
        "> <u>quote</u>",
        "",
        "**<em>bold</em>** and `<code>` and [<s>link</s>](https://example.com)",
        "",
        "| <th> |",
        "| --- |",
        "| <td> |",
        "",
        "```html",
        '<div onclick="x()">&amp;</div>',
        "```",
      ].join("\n"),
    );
    for (const tag of ["<b>", "<i>", "<u>", "<s>", "<div onclick"]) expect(out).not.toContain(tag);
    expect(out).toContain("&lt;b&gt;heading&lt;/b&gt;");
    expect(out).toContain("&lt;i&gt;item&lt;/i&gt;");
    expect(out).toContain("<strong>&lt;em&gt;bold&lt;/em&gt;</strong>");
    expect(out).toContain("<code>&lt;code&gt;</code>");
    expect(out).toContain("&lt;div onclick=&quot;x()&quot;&gt;&amp;amp;&lt;/div&gt;");
    expect(out).toContain("<th>&lt;th&gt;</th>");
    expect(out).toContain("<td>&lt;td&gt;</td>");
  });

  it("never turns a javascript:, data: or vbscript: link into an anchor", () => {
    for (const target of [
      "javascript:alert(1)",
      "JaVaScRiPt:alert(1)",
      "data:text/html,<script>1</script>",
      "vbscript:x",
      "//evil.example",
      "file:///etc/passwd",
    ]) {
      const out = html(`[click](${target})`);
      expect(out).not.toContain("<a");
      expect(out).not.toContain("href");
    }
  });

  it("cannot break out of the href attribute", () => {
    const out = html('[x](https://example.com/"onmouseover="alert(1))');
    expect(out).not.toMatch(/<a[^>]*\sonmouseover=/);
    const quoted = html("[x](https://example.com/?q=%22&a=<b>)");
    expect(quoted).toContain('href="https://example.com/?q=%22&amp;a=&lt;b&gt;"');
  });

  it("allows only http(s) and mailto targets", () => {
    expect(safeHref("https://example.com/a?b=1")).toBe("https://example.com/a?b=1");
    expect(safeHref("http://127.0.0.1:8201/")).toBe("http://127.0.0.1:8201/");
    expect(safeHref("mailto:sam@example.com")).toBe("mailto:sam@example.com");
    expect(safeHref(" javascript:alert(1)")).toBeNull();
    expect(safeHref("https://exa mple.com")).toBeNull();
    expect(safeHref("/relative")).toBeNull();
  });

  it("opens links in a new tab without an opener", () => {
    const out = html("See [the docs](https://example.com/docs).");
    expect(out).toContain('<a href="https://example.com/docs" target="_blank" rel="noopener noreferrer">the docs</a>');
  });
});

describe("blocks", () => {
  it("renders paragraphs, keeping single line breaks", () => {
    expect(html("One\ntwo\n\nThree")).toBe('<div class="md"><p>One\ntwo</p><p>Three</p></div>');
  });

  it("renders headings at two quiet levels", () => {
    expect(html("# Title\n\n### Sub")).toBe('<div class="md"><h3>Title</h3><h4>Sub</h4></div>');
  });

  it("renders unordered and ordered lists", () => {
    expect(html("- a\n- b")).toBe('<div class="md"><ul><li><span>a</span></li><li><span>b</span></li></ul></div>');
    expect(html("1. a\n2. b")).toBe('<div class="md"><ol><li><span>a</span></li><li><span>b</span></li></ol></div>');
    expect(html("3. c\n4. d")).toContain('<ol start="3">');
  });

  it("nests lists by indentation", () => {
    const out = html("- parent\n  - child one\n  - child two\n- next");
    expect(out).toBe(
      '<div class="md"><ul><li><p>parent</p><ul><li><span>child one</span></li><li><span>child two</span></li></ul></li><li><span>next</span></li></ul></div>',
    );
  });

  it("keeps a list going across blank lines and ends it at a paragraph", () => {
    const out = html("- a\n\n- b\n\nAfter");
    expect(out).toBe('<div class="md"><ul><li><span>a</span></li><li><span>b</span></li></ul><p>After</p></div>');
  });

  it("starts a list right after a paragraph line", () => {
    const out = html("Here’s what changed:\n- one\n- two");
    expect(out).toContain("<p>Here’s what changed:</p><ul>");
  });

  it("renders fenced code verbatim, without inline formatting", () => {
    const out = html("```js\nconst a = **1**;\n  indented `x`\n```\nafter");
    expect(out).toBe(
      '<div class="md"><pre data-lang="js"><code>const a = **1**;\n  indented `x`</code></pre><p>after</p></div>',
    );
  });

  it("treats an unclosed fence as code (text still streaming)", () => {
    const out = html("Working:\n```\nnpm run bu");
    expect(out).toBe('<div class="md"><p>Working:</p><pre><code>npm run bu</code></pre></div>');
  });

  it("renders block quotes, rules and tables", () => {
    expect(html("> quoted **text**")).toBe(
      '<div class="md"><blockquote><p>quoted <strong>text</strong></p></blockquote></div>',
    );
    expect(html("a\n\n---\n\nb")).toBe('<div class="md"><p>a</p><hr/><p>b</p></div>');
    const table = html("| Name | Share |\n| --- | ---: |\n| Acme | 34% |\n| Kite | 18% |");
    expect(table).toContain("<thead><tr><th>Name</th><th>Share</th></tr></thead>");
    expect(table).toContain("<tbody><tr><td>Acme</td><td>34%</td></tr><tr><td>Kite</td><td>18%</td></tr></tbody>");
  });

  it("renders nothing for empty text", () => {
    expect(html("")).toBe('<div class="md"></div>');
    expect(html("\n\n  \n")).toBe('<div class="md"></div>');
  });
});

describe("inline", () => {
  it("renders bold, italic, strike and code", () => {
    expect(html("**bold** *it* _it_ __bold__ ~~gone~~ `code`")).toBe(
      '<div class="md"><p><strong>bold</strong> <em>it</em> <em>it</em> <strong>bold</strong> <del>gone</del> <code>code</code></p></div>',
    );
  });

  it("nests emphasis", () => {
    expect(html("**bold and *both***")).toContain("<strong>bold and <em>both</em></strong>");
  });

  it("does not format inside code spans", () => {
    expect(html("`**not bold**` and ``a ` b``")).toBe(
      '<div class="md"><p><code>**not bold**</code> and <code>a ` b</code></p></div>',
    );
  });

  it("leaves snake_case, arithmetic and stray markers alone", () => {
    expect(html("open my_file_name.py")).toBe('<div class="md"><p>open my_file_name.py</p></div>');
    expect(html("2 * 3 * 4")).toBe('<div class="md"><p>2 * 3 * 4</p></div>');
    expect(html("an unclosed **bold")).toBe('<div class="md"><p>an unclosed **bold</p></div>');
    expect(html("a lone ` tick")).toBe('<div class="md"><p>a lone ` tick</p></div>');
  });

  it("honours backslash escapes", () => {
    expect(html("\\*not italic\\*")).toBe('<div class="md"><p>*not italic*</p></div>');
  });

  it("links bare URLs without swallowing trailing punctuation", () => {
    const out = html("See https://example.com/a_b?x=1. Or (http://127.0.0.1:8201/).");
    expect(out).toContain(
      '<a href="https://example.com/a_b?x=1" target="_blank" rel="noopener noreferrer">https://example.com/a_b?x=1</a>.',
    );
    expect(out).toContain(
      '(<a href="http://127.0.0.1:8201/" target="_blank" rel="noopener noreferrer">http://127.0.0.1:8201/</a>).',
    );
  });

  it("formats link text", () => {
    expect(html("[**bold** link](https://example.com)")).toContain(
      '<a href="https://example.com" target="_blank" rel="noopener noreferrer"><strong>bold</strong> link</a>',
    );
  });
});
