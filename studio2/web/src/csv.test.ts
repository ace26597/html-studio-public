import { describe, expect, it } from "vitest";
import { delimiterFor, isNumberCell, leftOutLine, parseDelimited, tableView } from "./csv";

describe("reading CSV", () => {
  it("reads quoted fields with commas, doubled quotes and line breaks inside", () => {
    const text = 'name,note\n"Smith, Jo","said ""hi""\nthen left"\nplain,\r\nlast,"x"';
    expect(parseDelimited(text)).toEqual([
      ["name", "note"],
      ["Smith, Jo", 'said "hi"\nthen left'],
      ["plain", ""],
      ["last", "x"],
    ]);
  });

  it("does not make a record of a final line break, and drops a byte-order mark", () => {
    expect(parseDelimited("﻿a,b\n1,2\n")).toEqual([
      ["a", "b"],
      ["1", "2"],
    ]);
  });

  it("reads TSV with tabs, and commas stay inside fields there", () => {
    expect(delimiterFor("files/rows.tsv")).toBe("\t");
    expect(delimiterFor("files/rows.csv")).toBe(",");
    expect(parseDelimited("a\tb\n1,5\t2", "\t")).toEqual([
      ["a", "b"],
      ["1,5", "2"],
    ]);
  });
});

describe("the table view", () => {
  it("pads short rows, finds number columns and skips blank lines", () => {
    const view = tableView("item,price,count\nTea,$4.50,1,200\n\nCake,(3.00)\n");
    expect(view.header).toEqual(["item", "price", "count", ""]);
    expect(view.rows).toEqual([
      ["Tea", "$4.50", "1", "200"],
      ["Cake", "(3.00)", "", ""],
    ]);
    expect(view.numeric).toEqual([false, true, true, true]);
    expect(leftOutLine(view)).toBeNull();
  });

  it("caps rows and columns and says how many were left out", () => {
    const header = Array.from({ length: 60 }, (_, i) => `c${i}`).join(",");
    const rows = Array.from({ length: 620 }, (_, r) => Array.from({ length: 60 }, (_, c) => r * c).join(","));
    const view = tableView([header, ...rows].join("\n"));
    expect(view.rows).toHaveLength(500);
    expect(view.header).toHaveLength(50);
    expect(view.rows[0]).toHaveLength(50);
    expect(view.rowsLeftOut).toBe(120);
    expect(view.columnsLeftOut).toBe(10);
    expect(leftOutLine(view)).toBe("Showing the first 500 of 620 rows and 50 of 60 columns. Show source for the whole file.");
    const narrow = tableView(["a,b", ...Array.from({ length: 3 }, () => "1,2")].join("\n"), ",", 2, 50);
    expect(leftOutLine(narrow)).toBe("Showing the first 2 of 3 rows. Show source for the whole file.");
  });

  it("knows a number when it sees one", () => {
    for (const cell of ["12", "-3.5", "1,234.50", "$9.99", "45%", "(120)", ".5"]) expect(isNumberCell(cell), cell).toBe(true);
    for (const cell of ["", "abc", "12 apples", "1,2,3", "-", "2026-11-18"]) expect(isNumberCell(cell), cell).toBe(false);
  });
});
