/** Export (CONTRACT.md): what each way out holds, in words for the dialog. */

import { formatBytes } from "./format";

export interface ExportOptions {
  name: string;
  app: boolean;
  pages: string[];
  page: string;
  html: {
    available: boolean;
    reason: string | null;
    bytes: number;
    inlined: number;
    embedded: { files: number; bytes: number };
    external: string[];
    notes: string[];
  };
  folder: {
    available: boolean;
    reason: string | null;
    files: number;
    bytes: number;
    run: string | null;
    tools: string[];
    needs: string[];
  };
}

export interface ExportWay {
  key: "html" | "folder" | "everything";
  title: string;
  /** One sentence on what it is. */
  what: string;
  available: boolean;
  /** Why not, when it is not available. */
  reason: string | null;
  /** Size and counts; empty when not available. */
  facts: string;
  /** What is not inside or will be missing: said, never hidden. */
  notes: string[];
  button: string;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** The three ways out, in the order the dialog shows them. */
export function exportWays(options: ExportOptions): ExportWay[] {
  const { html, folder } = options;
  const htmlNotes = [
    ...(html.external.length
      ? [`Needs the internet for what it still loads from ${html.external.join(", ")}.`]
      : html.available
        ? ["Needs no internet: nothing is loaded from elsewhere."]
        : []),
    ...html.notes,
  ];
  const inside = [
    html.inlined > 0 ? plural(html.inlined, "style, script or picture", "styles, scripts and pictures") : null,
    html.embedded.files > 0 ? `${plural(html.embedded.files, "data file", "data files")} (${formatBytes(html.embedded.bytes)})` : null,
  ].filter(Boolean);
  const folderNotes = options.app
    ? [
        ...(folder.tools.length ? [`To run it you need ${folder.tools.join("; ")}.`] : []),
        ...folder.needs.map((need) => `It used ${need}.`),
        "What the app saved while it ran here is not included: it starts empty.",
      ]
    : ["Any static host takes it as it is. A page that loads data files needs a web server; the note inside gives the one-line command."];
  return [
    {
      key: "html",
      title: "One HTML file",
      what: "The page with its styles, scripts, pictures and data inside. Opens from a double click, an email or a chat message.",
      available: html.available,
      reason: html.reason,
      facts: html.available ? [formatBytes(html.bytes), ...(inside.length ? [`holds ${inside.join(" and ")}`] : [])].join(" · ") : "",
      notes: html.available ? htmlNotes : [],
      button: "Download .html",
    },
    {
      key: "folder",
      title: options.app ? "A folder to run" : "A folder to host",
      what: options.app
        ? "The app's files with a note on how to start it and a script that does."
        : "The project's files with a note on how to open and host them.",
      available: folder.available,
      reason: folder.reason,
      facts: folder.available ? `${plural(folder.files, "file", "files")} · ${formatBytes(folder.bytes)} before zipping` : "",
      notes: folder.available ? folderNotes : [],
      button: "Download .zip",
    },
    {
      key: "everything",
      title: "Everything",
      what: "The working folder as it is: notes, attachments and the saved text of sources too. For keeping, not for sharing.",
      available: true,
      reason: null,
      facts: "",
      notes: [],
      button: "Download .zip",
    },
  ];
}
