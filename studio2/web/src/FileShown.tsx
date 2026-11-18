// Text files the Files panel shows as what they are: Markdown rendered, CSV and TSV as a table,
// SVG as the picture. The file is untrusted: the picture is an <img> (an SVG shown that way never
// runs script), and the Markdown renderer never turns raw HTML into elements.

import { useMemo } from "react";
import { delimiterFor, leftOutLine, tableView } from "./csv";
import { Markdown } from "./markdown";

export type ShownAs = "markdown" | "table" | "picture";

/** How a text file is shown when it is not shown as its source, or null: source only. */
export function shownAs(path: string): ShownAs | null {
  if (/\.(md|markdown)$/i.test(path)) return "markdown";
  if (/\.(csv|tsv|tab)$/i.test(path)) return "table";
  if (/\.svg$/i.test(path)) return "picture";
  return null;
}

/** The button that leaves the source for the shown view. */
export const SHOW_LABEL: Record<ShownAs, string> = {
  markdown: "Show formatted",
  table: "Show table",
  picture: "Show picture",
};

/** An SVG's text as an address an <img> can load: the picture, never a document that runs. */
export function svgImageSrc(text: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(text)}`;
}

export function CsvTable({ path, text }: { path: string; text: string }) {
  const view = useMemo(() => tableView(text, delimiterFor(path)), [path, text]);
  const cut = leftOutLine(view);
  if (!view.header.length) return <div className="panel-empty">This table is empty.</div>;
  return (
    <div className="file-shown file-table-wrap">
      {cut && <p className="file-table-cut">{cut}</p>}
      <div className="file-table-scroll" tabIndex={0} role="region" aria-label={`Table ${path}`}>
        <table className="file-table">
          <thead>
            <tr>
              {view.header.map((cell, j) => (
                <th key={j} className={view.numeric[j] ? "is-num" : undefined} scope="col">
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, j) => (
                  <td key={j} className={view.numeric[j] ? "is-num" : undefined}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function SvgPicture({ path, text }: { path: string; text: string }) {
  const src = useMemo(() => svgImageSrc(text), [text]);
  return (
    <div className="viewer-image">
      <img src={src} alt={path} />
    </div>
  );
}

export function MarkdownFile({ text }: { text: string }) {
  return (
    <div className="file-shown file-markdown">
      <Markdown text={text} />
    </div>
  );
}

/** The shown view of a text file (the buffer as edited, so a change to the source shows here). */
export function FileShown({ path, text, as }: { path: string; text: string; as: ShownAs }) {
  if (as === "table") return <CsvTable path={path} text={text} />;
  if (as === "picture") return <SvgPicture path={path} text={text} />;
  return <MarkdownFile text={text} />;
}
