// A small Markdown renderer for assistant text.
//
// Safety model: the source is parsed into React elements and every piece of
// model text ends up as a React text node, which React escapes. Nothing is ever
// passed to `dangerouslySetInnerHTML`, raw HTML in the source is shown as
// literal text, and link targets are limited to http(s) and mailto.

import { memo, type ReactNode } from "react";

type Block =
  | { t: "p"; text: string }
  | { t: "code"; lang: string; text: string }
  | { t: "h"; level: number; text: string }
  | { t: "list"; ordered: boolean; start: number; items: Block[][] }
  | { t: "quote"; blocks: Block[] }
  | { t: "hr" }
  | { t: "table"; head: string[]; rows: string[][] };

const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const LIST = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function indentOf(line: string): number {
  return line.length - line.trimStart().length;
}

function splitRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  return row.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

function startsBlock(line: string): boolean {
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || LIST.test(line) || QUOTE.test(line);
}

export function parseBlocks(lines: string[]): Block[] {
  const blocks: Block[] = [];
  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1];
      const body: string[] = [];
      i++;
      // An unclosed fence (text still streaming) runs to the end.
      while (i < n) {
        const close = /^\s{0,3}(`{3,}|~{3,})\s*$/.exec(lines[i]);
        if (close && close[1][0] === marker[0] && close[1].length >= marker.length) {
          i++;
          break;
        }
        body.push(lines[i]);
        i++;
      }
      blocks.push({ t: "code", lang: fence[2] ?? "", text: body.join("\n") });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ t: "h", level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }

    if (RULE.test(line)) {
      blocks.push({ t: "hr" });
      i++;
      continue;
    }

    if (QUOTE.test(line)) {
      const inner: string[] = [];
      while (i < n && QUOTE.test(lines[i])) {
        inner.push((QUOTE.exec(lines[i]) as RegExpExecArray)[1]);
        i++;
      }
      blocks.push({ t: "quote", blocks: parseBlocks(inner) });
      continue;
    }

    const first = LIST.exec(line);
    if (first) {
      const base = first[1].length;
      const ordered = /\d/.test(first[2]);
      const start = ordered ? parseInt(first[2], 10) : 1;
      const items: Block[][] = [];
      while (i < n) {
        const match = LIST.exec(lines[i]);
        if (!match || Math.abs(match[1].length - base) > 1 || /\d/.test(match[2]) !== ordered) break;
        const contentIndent = match[1].length + match[2].length + 1;
        const itemLines = [match[3]];
        i++;
        while (i < n) {
          const next = lines[i];
          if (!next.trim()) {
            let j = i;
            while (j < n && !lines[j].trim()) j++;
            if (j < n && indentOf(lines[j]) > base + 1) {
              itemLines.push("");
              i++;
              continue;
            }
            break;
          }
          if (indentOf(next) > base + 1) {
            itemLines.push(next.slice(Math.min(indentOf(next), contentIndent)));
            i++;
            continue;
          }
          break;
        }
        items.push(parseBlocks(itemLines));
        // A blank line followed by another item of the same list keeps the list going.
        let j = i;
        while (j < n && !lines[j].trim()) j++;
        const again = j < n ? LIST.exec(lines[j]) : null;
        if (j > i && again && Math.abs(again[1].length - base) <= 1 && /\d/.test(again[2]) === ordered) i = j;
      }
      blocks.push({ t: "list", ordered, start, items });
      continue;
    }

    if (line.includes("|") && i + 1 < n && TABLE_RULE.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const head = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < n && lines[i].trim() && lines[i].includes("|")) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      blocks.push({ t: "table", head, rows });
      continue;
    }

    const para: string[] = [];
    while (i < n && lines[i].trim() && !(para.length && startsBlock(lines[i]))) {
      para.push(lines[i].trim());
      i++;
    }
    blocks.push({ t: "p", text: para.join("\n") });
  }
  return blocks;
}

/** Only http(s) and mailto links become anchors; anything else stays plain text. */
export function safeHref(raw: string): string | null {
  const url = raw.trim();
  if (/^https?:\/\/[^\s]+$/i.test(url)) return url;
  if (/^mailto:[^\s]+$/i.test(url)) return url;
  return null;
}

// Order matters: escapes, code spans, then emphasis, links and bare URLs.
const INLINE = new RegExp(
  [
    "\\\\([\\\\`*_{}\\[\\]()#+\\-.!~|>])", // 1 backslash escape
    "(`+)(?!`)([\\s\\S]*?[^`])\\2(?!`)", // 2,3 code span
    "\\*\\*(?!\\s)([\\s\\S]+?)(?<!\\s)\\*\\*(?!\\*)", // 4 bold **
    "(?<![A-Za-z0-9])__(?!\\s)([\\s\\S]+?)(?<!\\s)__(?![A-Za-z0-9])", // 5 bold __
    "\\*(?![\\s*])([^*]+?)(?<!\\s)\\*", // 6 italic *
    "(?<![A-Za-z0-9_])_(?![\\s_])([^_]+?)(?<!\\s)_(?![A-Za-z0-9_])", // 7 italic _
    "~~(?!\\s)([\\s\\S]+?)(?<!\\s)~~", // 8 strike
    '\\[([^\\]\\n]+)\\]\\(\\s*([^)\\s]+)(?:\\s+"[^"]*")?\\s*\\)', // 9,10 link
    "(https?:\\/\\/[^\\s<>]*[^\\s<>.,;:!?'\")\\]}*_`])", // 11 bare URL
  ].join("|"),
  "g",
);

export function renderInline(text: string, keyPrefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  const re = new RegExp(INLINE.source, "g");
  let last = 0;
  let index = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const key = `${keyPrefix}.${index++}`;
    if (match[1] !== undefined) {
      out.push(match[1]);
    } else if (match[3] !== undefined) {
      out.push(<code key={key}>{match[3].replace(/^ (.+) $/s, "$1")}</code>);
    } else if (match[4] !== undefined) {
      out.push(<strong key={key}>{renderInline(match[4], key)}</strong>);
    } else if (match[5] !== undefined) {
      out.push(<strong key={key}>{renderInline(match[5], key)}</strong>);
    } else if (match[6] !== undefined) {
      out.push(<em key={key}>{renderInline(match[6], key)}</em>);
    } else if (match[7] !== undefined) {
      out.push(<em key={key}>{renderInline(match[7], key)}</em>);
    } else if (match[8] !== undefined) {
      out.push(<del key={key}>{renderInline(match[8], key)}</del>);
    } else if (match[9] !== undefined) {
      const href = safeHref(match[10]);
      out.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer">
            {renderInline(match[9], key)}
          </a>
        ) : (
          match[0]
        ),
      );
    } else if (match[11] !== undefined) {
      const href = safeHref(match[11]);
      out.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noopener noreferrer">
            {match[11]}
          </a>
        ) : (
          match[11]
        ),
      );
    }
    last = match.index + match[0].length;
    if (match[0].length === 0) re.lastIndex++;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function renderBlocks(blocks: Block[], keyPrefix: string, tight = false): ReactNode[] {
  return blocks.map((block, i) => {
    const key = `${keyPrefix}.${i}`;
    switch (block.t) {
      case "p":
        // A single paragraph inside a list item needs no wrapper.
        if (tight && blocks.length === 1) return <span key={key}>{renderInline(block.text, key)}</span>;
        return <p key={key}>{renderInline(block.text, key)}</p>;
      case "code":
        return (
          <pre key={key} data-lang={block.lang || undefined}>
            <code>{block.text}</code>
          </pre>
        );
      case "h": {
        // Chat text is small; clamp headings to two quiet levels.
        const Tag = block.level <= 2 ? "h3" : "h4";
        return <Tag key={key}>{renderInline(block.text, key)}</Tag>;
      }
      case "hr":
        return <hr key={key} />;
      case "quote":
        return <blockquote key={key}>{renderBlocks(block.blocks, key)}</blockquote>;
      case "list": {
        const items = block.items.map((item, j) => (
          <li key={`${key}.${j}`}>{renderBlocks(item, `${key}.${j}`, true)}</li>
        ));
        return block.ordered ? (
          <ol key={key} start={block.start !== 1 ? block.start : undefined}>
            {items}
          </ol>
        ) : (
          <ul key={key}>{items}</ul>
        );
      }
      case "table":
        return (
          <div key={key} className="md-table">
            <table>
              <thead>
                <tr>
                  {block.head.map((cell, j) => (
                    <th key={j}>{renderInline(cell, `${key}.h${j}`)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, r) => (
                  <tr key={r}>
                    {block.head.map((_, j) => (
                      <td key={j}>{renderInline(row[j] ?? "", `${key}.${r}.${j}`)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
    }
  });
}

export function renderMarkdown(source: string): ReactNode[] {
  const lines = source.replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n");
  return renderBlocks(parseBlocks(lines), "b");
}

export const Markdown = memo(function Markdown({ text, findKey }: { text: string; /** `data-find`, for Find to scroll to it. */ findKey?: string }) {
  return (
    <div className="md" data-find={findKey}>
      {renderMarkdown(text)}
    </div>
  );
});
