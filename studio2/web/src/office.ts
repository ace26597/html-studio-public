// Office documents in the Files panel: which files they are, where their
// rendered preview lives, and where generated preview folders sit in the tree.

export type OfficeKind = "presentation" | "document" | "workbook" | "pdf";

const KINDS: [RegExp, OfficeKind][] = [
  [/\.(pptx|ppt|odp)$/i, "presentation"],
  [/\.(docx|doc|rtf|odt)$/i, "document"],
  [/\.(xlsx|xlsm|xls|ods)$/i, "workbook"],
  [/\.pdf$/i, "pdf"],
];

/** The kind of an Office (or PDF) file, or null for anything else. */
export function officeKind(path: string): OfficeKind | null {
  for (const [pattern, kind] of KINDS) if (pattern.test(path)) return kind;
  return null;
}

export const OFFICE_KIND_LABEL: Record<OfficeKind, string> = {
  presentation: "Presentation",
  document: "Document",
  workbook: "Spreadsheet",
  pdf: "PDF",
};

/** `files/deck.pptx` → `files/deck.pptx.preview/index.html` (written by `render_document`). */
export function previewIndexPath(path: string): string {
  return `${path}.preview/index.html`;
}

/** The rendered viewer for `path`, when the file listing has one. */
export function findPreview(path: string, listing: readonly { path: string }[]): string | null {
  const wanted = previewIndexPath(path);
  return listing.some((entry) => entry.path === wanted) ? wanted : null;
}

/** A generated `<file>.preview` folder. */
export function isPreviewDir(name: string): boolean {
  return /\.preview$/i.test(name) && name.length > ".preview".length;
}

export type TreeItem<D, F> = { type: "dir"; dir: D; preview: boolean } | { type: "file"; file: F };

/**
 * The order of one folder's contents: ordinary folders first, then files, each
 * file followed by its own `.preview` folder; preview folders whose file is
 * missing go last. Inputs are already sorted by name.
 */
export function orderTreeItems<D extends { name: string }, F extends { path: string }>(
  dirs: readonly D[],
  files: readonly F[],
): TreeItem<D, F>[] {
  const plain = dirs.filter((dir) => !isPreviewDir(dir.name));
  const previews = dirs.filter((dir) => isPreviewDir(dir.name));
  const used = new Set<D>();
  const out: TreeItem<D, F>[] = plain.map((dir) => ({ type: "dir", dir, preview: false }));
  for (const file of files) {
    out.push({ type: "file", file });
    const name = file.path.split("/").pop() ?? file.path;
    const own = previews.find((dir) => dir.name === `${name}.preview`);
    if (own) {
      used.add(own);
      out.push({ type: "dir", dir: own, preview: true });
    }
  }
  for (const dir of previews) if (!used.has(dir)) out.push({ type: "dir", dir, preview: true });
  return out;
}
