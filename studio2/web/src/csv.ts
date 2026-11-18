// CSV and TSV files read into a table for the Files panel: quoted fields (with commas, tabs,
// doubled quotes and line breaks inside them), then cut to what a viewer can show.

export const TABLE_MAX_ROWS = 500;
export const TABLE_MAX_COLUMNS = 50;

/** The delimiter for a file: a tab for `.tsv` (and `.tab`), a comma otherwise. */
export function delimiterFor(path: string): "," | "\t" {
  return /\.(tsv|tab)$/i.test(path) ? "\t" : ",";
}

/** Every record of the text, each a list of fields. A last empty line is not a record. */
export function parseDelimited(text: string, delimiter: string = ","): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let i = text.charCodeAt(0) === 0xfeff ? 1 : 0; // a byte-order mark is not part of the first name
  const n = text.length;
  while (i < n) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        quoted = false;
        i++;
        continue;
      }
      field += ch;
      i++;
      continue;
    }
    if (ch === '"' && field === "") {
      quoted = true;
      i++;
    } else if (ch === delimiter) {
      row.push(field);
      field = "";
      i++;
    } else if (ch === "\n" || ch === "\r") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      i += ch === "\r" && text[i + 1] === "\n" ? 2 : 1;
    } else {
      field += ch;
      i++;
    }
  }
  // The last record, unless the text ended with a line break.
  if (field !== "" || row.length > 0 || quoted) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

export interface TableView {
  header: string[];
  rows: string[][];
  /** Per shown column: every filled cell below the header is a number (right-aligned). */
  numeric: boolean[];
  /** Records below the header in the file. */
  totalRows: number;
  /** The widest record's width. */
  totalColumns: number;
  rowsLeftOut: number;
  columnsLeftOut: number;
}

const NUMBER = /^[-+]?[$€£¥₹]?\(?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?\)?%?$/;

/** A cell that reads as a number: `12`, `-3.5`, `1,234.50`, `$9.99`, `45%`, `(120)`. */
export function isNumberCell(value: string): boolean {
  const cell = value.trim();
  return cell !== "" && /\d/.test(cell) && NUMBER.test(cell);
}

/** The first record as the header, then at most `maxRows` records and `maxColumns` columns. */
export function tableView(
  text: string,
  delimiter: string = ",",
  maxRows: number = TABLE_MAX_ROWS,
  maxColumns: number = TABLE_MAX_COLUMNS,
): TableView {
  // A blank line is not a record.
  const records = parseDelimited(text, delimiter).filter((record) => record.length > 1 || record[0] !== "");
  const totalColumns = records.reduce((widest, record) => Math.max(widest, record.length), 0);
  const width = Math.min(totalColumns, maxColumns);
  const pad = (record: string[]) => Array.from({ length: width }, (_, j) => record[j] ?? "");
  const header = records.length ? pad(records[0]) : [];
  const body = records.slice(1);
  const rows = body.slice(0, maxRows).map(pad);
  const numeric = header.map((_, j) => {
    let seen = false;
    for (const row of rows) {
      const cell = row[j].trim();
      if (!cell) continue;
      if (!isNumberCell(cell)) return false;
      seen = true;
    }
    return seen;
  });
  return {
    header,
    rows,
    numeric,
    totalRows: body.length,
    totalColumns,
    rowsLeftOut: Math.max(0, body.length - maxRows),
    columnsLeftOut: Math.max(0, totalColumns - maxColumns),
  };
}

/** "Showing the first 500 of 1,204 rows and 50 of 63 columns." — null when nothing was cut. */
export function leftOutLine(view: TableView): string | null {
  const parts: string[] = [];
  const count = (value: number) => value.toLocaleString("en-US");
  if (view.rowsLeftOut > 0) parts.push(`the first ${count(view.rows.length)} of ${count(view.totalRows)} rows`);
  if (view.columnsLeftOut > 0) parts.push(`${parts.length ? "" : "the first "}${count(view.header.length)} of ${count(view.totalColumns)} columns`);
  return parts.length ? `Showing ${parts.join(" and ")}. Show source for the whole file.` : null;
}
