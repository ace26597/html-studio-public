"""The file check (CONTRACT.md, "Verify content reaches files")."""

import json
import zipfile
from xml.sax.saxutils import escape

import pytest

from studio2_server import effort, filecheck, office, tools
from studio2_server.filecheck import check_file, figures_in, linked_files

openpyxl = pytest.importorskip("openpyxl")
needs_office = pytest.mark.skipif(office.reason() is not None, reason="LibreOffice is not installed")

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


# -- small real files, written the way Office writes them (a zip of XML parts) --------------------------
def _run(text, superscript=False):
    props = '<a:rPr lang="en-US" baseline="30000"/>' if superscript else '<a:rPr lang="en-US"/>'
    return f"<a:r>{props}<a:t>{escape(text)}</a:t></a:r>"


def _shape(n, name, paragraphs, placeholder=None):
    ph = f'<p:nvPr><p:ph type="{placeholder}"/></p:nvPr>' if placeholder else "<p:nvPr/>"
    body = "".join(f"<a:p>{''.join(_run(*r) if isinstance(r, tuple) else _run(r) for r in p)}</a:p>"
                   for p in paragraphs)
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{n}" name="{name}"/><p:cNvSpPr/>{ph}</p:nvSpPr><p:spPr/>'
            f"<p:txBody><a:bodyPr/><a:lstStyle/>{body}</p:txBody></p:sp>")


def _table(n, rows):
    cells = "".join("<a:tr h=\"370840\">" + "".join(
        f"<a:tc><a:txBody><a:bodyPr/><a:p>{_run(c)}</a:p></a:txBody></a:tc>" for c in row) + "</a:tr>" for row in rows)
    return (f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{n}" name="Table {n}"/><p:cNvGraphicFramePr/><p:nvPr/>'
            f'</p:nvGraphicFramePr><p:xfrm/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/'
            f'2006/table"><a:tbl><a:tblGrid/>{cells}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')


def make_pptx(path, slides):
    """slides: lists of shape XML. Written in the reverse file order, to prove the deck's order is used."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
                   + "".join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.'
                             'openxmlformats-officedocument.presentationml.slide+xml"/>' for i in range(1, len(slides) + 1))
                   + "</Types>")
        z.writestr("_rels/.rels", f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{R}/officeDocument" '
                                  'Target="ppt/presentation.xml"/></Relationships>')
        count = len(slides)
        ids = "".join(f'<p:sldId id="{256 + i}" r:id="rId{i + 1}"/>' for i in range(count))
        z.writestr("ppt/presentation.xml", f'<p:presentation xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">'
                                           f"<p:sldIdLst>{ids}</p:sldIdLst></p:presentation>")
        # Slide i of the deck is stored as file slide<count - i>.xml.
        rels = "".join(f'<Relationship Id="rId{i + 1}" Type="{R}/slide" Target="slides/slide{count - i}.xml"/>'
                       for i in range(count))
        z.writestr("ppt/_rels/presentation.xml.rels", f'<Relationships xmlns="{PKG}">{rels}</Relationships>')
        for i, shapes in enumerate(slides):
            z.writestr(f"ppt/slides/slide{count - i}.xml",
                       f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree><p:nvGrpSpPr>'
                       f'<p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>{"".join(shapes)}'
                       "</p:spTree></p:cSld></p:sld>")


def _wp(text, style=None, superscript_tail=None):
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    tail = (f'<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>{escape(superscript_tail)}</w:t></w:r>'
            if superscript_tail else "")
    return f'<w:p>{props}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>{tail}</w:p>'


def make_docx(path, body, styles=None):
    with zipfile.ZipFile(path, "w") as z:
        if styles:
            z.writestr("word/styles.xml", f'<w:styles xmlns:w="{W}">{styles}</w:styles>')
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels", f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{R}/officeDocument" '
                                  'Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                        f'<w:document xmlns:w="{W}"><w:body>{body}<w:sectPr/></w:body></w:document>')


def _wtable(rows):
    return "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc>{_wp(c)}</w:tc>" for c in row) + "</w:tr>"
                               for row in rows) + "</w:tbl>"


def make_pdf(path, pages):
    """A real PDF with a text layer: one Helvetica text line per entry, one page per list."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        stream = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(
            f"({line.replace('(', chr(92) + '(').replace(')', chr(92) + ')')}) Tj T*" for line in lines) + " ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                       f"/Contents {len(objects)} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def vouching_project(root):
    """A saved source, a data file and a page check, as a real project has them."""
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "s1.txt").write_text("In 2025 revenue was $4,218,950 and the margin was 12.4 percent. "
                                             "Net loss of 1,234 thousand.")
    (root / "sources" / "index.json").write_text(json.dumps({"sources": [
        {"id": "s1", "title": "Annual report", "url": "https://example.com/ar", "fetched_at": "2026-11-13T00:00:00Z",
         "kind": "html", "chars": 90, "path": "sources/s1.txt"}]}))
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "sales.csv").write_text("region,units\nNorth,1520\nSouth,980\n")
    (root / "checks").mkdir(exist_ok=True)
    (root / "checks" / "index.html.json").write_text(json.dumps({"page": "index.html", "items": [
        {"n": 1, "text": "37 stores", "status": "verified", "where": "Footprint", "found": None},
        {"n": 2, "text": "$412", "status": "derived", "where": "Unit economics", "reason": "s1 revenue ÷ units"},
        {"n": 3, "text": "99 sites", "status": "failed", "where": "x"}]}))


def by_text(result):
    return {item["text"]: item for item in result["items"]}


# -- what is a figure ------------------------------------------------------------------------------
@pytest.mark.parametrize("text, figures", [
    ("Revenue reached $4.3 billion in 2025", ["$4.3 billion"]),
    ("Units: 4.2 million, up 12% (11.5% before)", ["4.2 million", "12%", "11.5%"]),
    ("Loss of (1,234) and -56.7", ["(1,234)", "-56.7"]),
    ("₹12,34,567 and 4.2 cr and Rs 500", ["₹12,34,567", "4.2 cr", "Rs 500"]),
    ("4.2m and 300k, 1.5bn", ["4.2m", "300k", "1.5bn"]),
    ("2,025 units and $2025 but in 2025", ["2,025", "$2025"]),
    ("34 GWh and 550W panels", ["34", "550"]),
])
def test_figures_are_read_with_their_currency_scale_percent_and_sign(text, figures):
    found, _skipped = figures_in(text, "here")
    assert [f["text"] for f in found] == figures


def test_values_are_normalised():
    (bn, pct, neg, lakh), _ = figures_in("$4.3 billion, 12.5%, (1,234), ₹12,34,567", "")
    assert bn["value"] == 4_300_000_000 and bn["decimals"] == 1 and bn["scale"] == 9
    assert pct["value"] == 12.5 and pct["percent"] and neg["negative"] and neg["value"] == 1234
    assert lakh["value"] == 1_234_567


@pytest.mark.parametrize("text", [
    "In 2025, and again in 2024–25 and FY24",                         # years, year ranges, fiscal years
    "On 3 March 2025, Mar 2025, 2025-03-03, 3/3/25 at 10:30",         # dates and times
    "See slide 4, page 12, figure 3, table 7, section 9, step 14 and #4411",   # slide, page and list numbers
    "1. First point",                                                 # numbering a line
    "(3) third point",
    "As reported [1] and [12]",                                       # reference markers
    "the 21st time, the 3rd",                                         # ordinals
    "Call +1 415 555 0100, (415) 555-0100 or 415-555-0100",           # phones
    "Order 4411-22-90, part 4411A, a 5G phone, account 123456789",   # ids
    "v2.1, version 3 and 1.2.3",                                      # versions
    "https://example.com/2025/99 and a1990@example.com",              # addresses
    "3 regions and 12 months",                                        # small whole numbers
])
def test_what_is_not_a_figure_is_skipped_and_counted(text):
    found, skipped = figures_in(text, "")
    assert found == [] and skipped >= 1


def test_a_lone_page_number_line_in_a_pdf_is_skipped_but_not_a_number_in_a_sentence():
    found, _ = figures_in("17\nPage 3 of 12\n4 / 9\nWe opened 17 stores", "", pdf=True)
    assert [f["context"] for f in found] == ["We opened 17 stores"]
    assert [f["text"] for f in figures_in("17", "")[0]] == ["17"]      # a table cell of its own is a figure


# -- matching ------------------------------------------------------------------------------------
def test_a_deck_is_read_in_its_own_order_with_slides_shapes_and_tables(tmp_path):
    vouching_project(tmp_path)
    (tmp_path / "files").mkdir()
    make_pptx(tmp_path / "files" / "deck.pptx", [
        [_shape(2, "Title 1", [["Results for 2025"]], "title"),
         _shape(3, "Slide Number 4", [["1"]], "sldNum")],
        [_shape(2, "Title 1", [["Revenue reached $4.3 billion"]], "title"),
         _shape(3, "Content 2", [["Revenue was $4.2 million", ("1", True)], ["Margin 12%"], ["2,500 units sold"]]),
         _table(4, [["Region", "Units"], ["North", "1,520"], ["West", "1,777"]])],
        [_shape(2, "Content 2", [["We run 37 stores at $412 each, see slide 2"], ["Loss of -1,234 thousand"]])],
    ])
    result = check_file(tmp_path, "files/deck.pptx")
    items = by_text(result)
    assert result["kind"] == "presentation" and result["error"] is None
    wrong = items["$4.3 billion"]
    assert (wrong["status"], wrong["where"]) == ("not_found", "Slide 2, title")
    rounded = items["$4.2 million"]                                   # 4,218,950 in s1, shown in millions
    assert (rounded["status"], rounded["match"], rounded["via"], rounded["ref"], rounded["rounded_from"]) == (
        "found", "rounded", "source", "s1", "4218950")
    assert rounded["where"] == "Slide 2, Content 2"
    assert (items["12%"]["status"], items["12%"]["match"]) == ("found", "rounded")       # 12.4 percent
    assert (items["2,500"]["status"], items["2,500"]["via"], items["2,500"]["ref"]) == (
        "found", "data", "data/sales.csv, column units (total)")
    assert items["1,520"]["where"] == "Slide 2, table row 2, column 2" and items["1,520"]["match"] == "exact"
    assert items["1,777"]["status"] == "not_found"
    assert (items["37"]["status"], items["37"]["via"], items["37"]["ref"]) == ("found", "page", "index.html › Footprint")
    assert items["$412"]["status"] == "derived" and "derived" in items["$412"]["note"]
    assert items["-1,234 thousand"]["status"] == "found"                                 # 1,234 thousand in s1
    assert "99" not in json.dumps([i["text"] for i in result["items"]])                   # a failed page item vouches nothing
    assert "1" not in items                                     # superscript and the slide-number placeholder: not read
    assert result["summary"]["skipped"] == 2                    # "2025" alone, "slide 2"
    assert result["summary"] == {"figures": 9, "found": 6, "rounded": 2, "derived": 1, "not_found": 2,
                                 "skipped": result["summary"]["skipped"], "ledger": 0, "placeholders": 0,
                                 "conflicts": 0}
    assert result["saved"] == "checks/files-deck.pptx.json"
    assert json.loads((tmp_path / result["saved"]).read_text())["file"] == "files/deck.pptx"


def test_truncation_is_not_rounding_and_a_figure_shown_in_another_unit_is_not_found(tmp_path):
    vouching_project(tmp_path)
    make_docx(tmp_path / "memo.docx", _wp("Revenue was $4.21 million.") + _wp("Revenue was 4,218.95 thousand.")
              + _wp("Revenue was 4.21895m."))
    items = by_text(check_file(tmp_path, "memo.docx", save=False))
    assert items["$4.21 million"]["status"] == "not_found"
    assert items["4,218.95 thousand"]["match"] == "exact" and items["4.21895m"]["match"] == "exact"
    assert not (tmp_path / "checks" / "memo.docx.json").exists()


def test_a_word_document_says_paragraph_table_and_heading(tmp_path):
    vouching_project(tmp_path)
    body = (_wp("Annual review", "Title") + _wp("Summary", "Heading1")
            + _wp("Revenue was $4,218,950 in 2025.", superscript_tail="2")
            + _wp("Results", "Heading2") + _wp("Margin fell to 9.8%.")
            + _wtable([["Region", "Units"], ["North", "1,520"], ["South", "990"]]))
    make_docx(tmp_path / "report.docx", body)
    result = check_file(tmp_path, "report.docx")
    items = by_text(result)
    assert result["kind"] == "document"
    assert items["$4,218,950"]["where"] == 'Paragraph 3 under "Summary"' and items["$4,218,950"]["match"] == "exact"
    assert items["9.8%"]["status"] == "not_found" and items["9.8%"]["where"] == 'Paragraph 5 under "Results"'
    assert items["990"]["where"] == 'Table 1, row 3, column 2 under "Results"' and items["990"]["status"] == "not_found"
    assert items["1,520"]["status"] == "found"
    assert "2" not in items                                           # the footnote mark is superscript


def _workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["Region", "2025", "Share"])
    ws.append(["North", 1520, 0.608])
    ws.append(["South", 990, 0.392])
    ws.append(["Total", "=SUM(B2:B3)", None])
    ws.append(["Revenue", 4218950, None])
    ws.append(["Note", "Revenue was $4.3 billion", None])
    ws.append(["Opened", 2019, None])
    ws["C2"].number_format = "0.0%"
    ws["C3"].number_format = "0.0%"
    wb.save(path)


def test_a_workbook_has_formulas_derived_and_typed_numbers_compared(tmp_path):
    vouching_project(tmp_path)
    (tmp_path / "data" / "shares.json").write_text(json.dumps({"north": {"share": "60.8%"}}))
    _workbook(tmp_path / "book.xlsx")
    result = check_file(tmp_path, "book.xlsx")
    items = {item["where"].split(" ")[0]: item for item in result["items"]}
    assert result["kind"] == "workbook"
    total = items["Summary!B4"]
    assert total["status"] == "derived" and total["note"].startswith("=SUM(B2:B3)") and "render_document" in total["note"]
    assert items["Summary!B2"]["status"] == "found" and items["Summary!B2"]["where"] == "Summary!B2 (North, 2025)"
    assert items["Summary!C2"]["text"] == "60.8%" and items["Summary!C2"]["status"] == "found"
    assert items["Summary!C3"]["status"] == "not_found"               # 39.2% is nowhere
    assert items["Summary!B3"]["status"] == "not_found"               # 990: the data says 980
    assert items["Summary!B5"]["match"] == "exact"
    assert items["Summary!B6"]["text"] == "$4.3 billion" and items["Summary!B6"]["status"] == "not_found"
    assert "Summary!B7" not in items                                  # a year standing alone


@needs_office
def test_a_workbook_formula_shows_its_calculated_value_after_rendering(tmp_path):
    vouching_project(tmp_path)
    _workbook(tmp_path / "book.xlsx")
    office.render(tmp_path, "book.xlsx")
    total = next(i for i in check_file(tmp_path, "book.xlsx", save=False)["items"] if i["where"].startswith("Summary!B4"))
    assert total["status"] == "derived" and total["text"] == "2510" and "render_document" not in total["note"]


def test_a_pdf_is_read_page_by_page(tmp_path):
    vouching_project(tmp_path)
    make_pdf(tmp_path / "brief.pdf", [["Revenue was $4,218,950.", "1"], ["Costs were $3.9 million.", "Page 2 of 2"]])
    result = check_file(tmp_path, "brief.pdf")
    items = by_text(result)
    assert result["kind"] == "pdf" and result["error"] is None
    assert items["$4,218,950"]["where"] == "Page 1" and items["$4,218,950"]["status"] == "found"
    assert items["$3.9 million"]["where"] == "Page 2" and items["$3.9 million"]["status"] == "not_found"
    assert result["summary"]["figures"] == 2


def test_a_pdf_without_text_and_a_broken_file_are_said_never_passed(tmp_path):
    make_pdf(tmp_path / "scan.pdf", [[]])
    result = check_file(tmp_path, "scan.pdf")
    assert "no text layer" in result["error"] and result["items"] == []
    (tmp_path / "broken.pptx").write_bytes(b"not a zip")
    broken = check_file(tmp_path, "broken.pptx")
    assert broken["error"].startswith("The file could not be read") and broken["summary"]["figures"] == 0
    assert "Nothing in this file was checked" in filecheck.compact(broken)["next"]


@needs_office
def test_the_test_files_are_real_office_files(tmp_path):
    """LibreOffice opens the hand-written document; its PDF is then checked like any other."""
    vouching_project(tmp_path)
    make_docx(tmp_path / "memo.docx", _wp("Results", "Heading1") + _wp("Revenue was $4,218,950 and costs $7.7 million."))
    pdf = office.convert(tmp_path / "memo.docx", "pdf", tmp_path / "out")
    items = by_text(check_file(tmp_path, pdf.relative_to(tmp_path).as_posix(), save=False))
    assert items["$4,218,950"]["status"] == "found" and items["$7.7 million"]["status"] == "not_found"


def test_the_files_a_page_links_or_frames_are_found(tmp_path):
    (tmp_path / "files").mkdir()
    for name in ("deck.pptx", "book.xlsx", "memo.docx", "notes.txt"):
        (tmp_path / "files" / name).write_bytes(b"x")
    (tmp_path / "index.html").write_text(
        '<a href="files/deck.pptx" download>Deck</a><iframe src="files/book.xlsx.preview/index.html"></iframe>'
        '<a href="https://example.com/x.pdf">x</a><a href="files/missing.docx">m</a><a href="files/notes.txt">n</a>'
        '<object data="./files/memo.docx?v=2"></object><a href="files/deck.pptx#p2">again</a>'
        '<a href="../outside.pptx">out</a>')
    assert linked_files(tmp_path) == ["files/deck.pptx", "files/book.xlsx", "files/memo.docx"]


# -- the tool ------------------------------------------------------------------------------------
async def test_the_tool_answers_compactly_with_the_not_found_ones_first(studio, project):
    workspace = studio.store.workspace(project["id"])
    vouching_project(workspace)
    make_pptx(workspace / "deck.pptx", [[_shape(2, "Title 1", [["Revenue $4.3 billion, $4.2 million, 37 stores"]],
                                                "title")]])
    answer = await tools.call(studio, project["id"], "check_file", {"path": "deck.pptx"})
    assert not answer.get("is_error"), answer
    result = json.loads(answer["content"][0]["text"])
    assert [i["text"] for i in result["not_found"]] == ["$4.3 billion"]
    assert result["not_found"][0]["where"] == "Slide 1, title" and "value" not in result["not_found"][0]
    assert [i["rounded_from"] for i in result["rounded"]] == ["4218950"]
    assert result["saved"] == "checks/deck.pptx.json" and "not_found" in result["next"]
    assert result["summary"]["not_found"] == 1
    for path, says in (("notes.txt", ".pptx, .docx, .xlsx and .pdf"), ("none.pptx", "no file"),
                       ("../../x.pptx", "no file"), ("", "no file")):
        if path == "notes.txt":
            (workspace / path).write_text("x")
        refused = await tools.call(studio, project["id"], "check_file", {"path": path})
        assert refused.get("is_error") and says in refused["content"][0]["text"], path


def test_the_tool_sits_after_check_page_and_has_a_title(tmp_path):
    from studio2_server.harness.claude import describe_tool
    names = [name for name, *_ in tools.TOOLS]
    assert names[names.index("check_page") + 1] == "check_file"
    assert describe_tool("mcp__studio__check_file", {"path": "files/deck.pptx"}, tmp_path) == (
        "read", "Check the figures in files/deck.pptx")


# -- the wording ---------------------------------------------------------------------------------
def test_the_wording_sends_the_check_to_the_files():
    from pathlib import Path
    text = effort.VERIFY
    assert "check_file" in text and "Office or PDF file" in text and "formulas too" in text
    assert "check_file on every file you changed" in text and "run check_page again" in text
    library = Path(__file__).resolve().parents[1] / "library"
    checker = (library / "agents" / "checker.md").read_text()
    assert "mcp__studio__check_file" in checker and "not_found" in checker and "Recompute `derived`" in checker
    skill = (library / "skills" / "office-files" / "SKILL.md").read_text()
    assert "Run `check_file` on the file" in skill


# -- the studio's own re-check -------------------------------------------------------------------
async def test_the_studio_rechecks_the_files_after_a_verification(client, studio, project):
    from conftest import run_turn
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    vouching_project(workspace)
    (workspace / "files").mkdir()
    make_pptx(workspace / "files" / "deck.pptx", [
        [_shape(2, "Title 1", [["Revenue $4.2 million"]], "title")],
        [_shape(2, "Title 1", [["Revenue reached $4.3 billion"]], "title"), _shape(3, "Content 2", [["Up 9.9%"]])]])
    make_docx(workspace / "memo.docx", _wp("Results", "Heading1") + _wp("Costs were $7.7 million."))
    (workspace / "index.html").write_text(
        '<main><p>Revenue was <span data-src="s1" data-quote="revenue was $4,218,950">$4,218,950</span>.</p>'
        '<iframe src="files/deck.pptx.preview/index.html"></iframe><a href="files/deck.pptx">Deck</a></main>')
    events = await run_turn(client, studio, pid, "check file memo.docx", options={"verify": True})
    kinds = [e["type"] for e in events]
    assert kinds.index("verification") < kinds.index("verification_recheck") < kinds.index("turn_finished")
    recheck = next(e for e in events if e["type"] == "verification_recheck")
    assert [(p["page"], p["kind"]) for p in recheck["pages"]] == [
        ("index.html", "page"), ("memo.docx", "file"), ("files/deck.pptx", "file")]
    deck = recheck["pages"][2]
    assert (deck["checked"], deck["found"], deck["not_found"], deck["rounded"], deck["verified"], deck["failed"],
            deck["error"]) == (3, 1, 2, 1, 1, 2, None)
    assert recheck["agrees"] is False and recheck["problems_total"] == 3
    assert recheck["problems"] == [
        {"page": "memo.docx", "kind": "file", "what": "$7.7 million", "where": 'Paragraph 2 under "Results"',
         "why": filecheck.NOT_FOUND_WHY},
        {"page": "files/deck.pptx", "kind": "file", "what": "$4.3 billion", "where": "Slide 2, title",
         "why": filecheck.NOT_FOUND_WHY},
        {"page": "files/deck.pptx", "kind": "file", "what": "9.9%", "where": "Slide 2, Content 2",
         "why": filecheck.NOT_FOUND_WHY}]
    assert not (workspace / "checks" / "files-deck.pptx.json").exists()       # the re-check saves nothing
    assert (workspace / "checks" / "memo.docx.json").exists()                 # the agent's own check is saved
    # The page's problems come before the files'.
    (workspace / "index.html").write_text(
        '<main><p><span data-src="s1" data-quote="this sentence is not in the source">12%</span></p>'
        '<a href="files/deck.pptx">Deck</a></main>')
    events = await run_turn(client, studio, pid, "Verify", options={"check_only": True})
    recheck = next(e for e in events if e["type"] == "verification_recheck")
    assert [(p["kind"], p["what"]) for p in recheck["problems"]] == [
        ("page", "12%"), ("file", "$4.3 billion"), ("file", "9.9%")]
    # Only files, no index.html: still re-checked; a file that cannot be read is said, never passed.
    (workspace / "index.html").unlink()
    (workspace / "broken.pptx").write_bytes(b"not a zip")
    events = await run_turn(client, studio, pid, "check file broken.pptx", options={"verify": True})
    recheck = next(e for e in events if e["type"] == "verification_recheck")
    assert [p["page"] for p in recheck["pages"]] == ["broken.pptx"]
    assert recheck["pages"][0]["error"].startswith("The file could not be read") and recheck["agrees"] is False


def test_a_workbook_too_big_to_read_whole_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(filecheck, "MAX_CELLS", 5)
    _workbook(tmp_path / "book.xlsx")
    result = check_file(tmp_path, "book.xlsx", save=False)
    assert result["not_read"].startswith("Only the first 5 cells were read")
    assert "Say so in your report" in filecheck.compact(result)["next"]


def test_a_heading_is_known_by_its_style_name_not_only_its_id(tmp_path):
    styles = ('<w:style w:type="paragraph" w:styleId="Kop2"><w:name w:val="heading 2"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="MyHead"><w:name w:val="My head"/><w:basedOn w:val="Kop2"/></w:style>')
    make_docx(tmp_path / "nl.docx", _wp("Omzet", "Kop2") + _wp("Omzet was 4.218.950 en 1,777 eenheden.")
              + _wp("Kosten", "MyHead") + _wp("Costs 3,333."), styles)
    items = by_text(check_file(tmp_path, "nl.docx", save=False))
    assert items["1,777"]["where"] == 'Paragraph 2 under "Omzet"' and items["3,333"]["where"] == 'Paragraph 4 under "Kosten"'


@pytest.mark.parametrize("text, dates", [
    ("Margin 12", []),                      # a word that starts like a month is not one
    ("Marketing 2025", []),
    ("Decade 10", []),
    ("Mar 2025", ["Mar 2025"]),             # not "Mar 20" with "25" left over
    ("March 3, 2025", ["March 3, 2025"]),
    ("3 Mar 2025", ["3 Mar 2025"]),
    ("Sept. 12", ["Sept. 12"]),
    ("Jan 5th", ["Jan 5th"]),
])
def test_the_page_check_and_the_file_check_share_one_date_pattern(text, dates):
    from studio2_server import check, filecheck
    assert filecheck.DATE is check.DATE
    assert [m.group(0) for m in check.DATE.finditer(text)] == dates


def test_numbers_written_inside_text_in_a_data_file_vouch_and_geojson_is_read(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "feed.geojson").write_text(json.dumps(
        {"features": [{"properties": {"mag": 6.6, "place": "148 km WSW of Tual, Indonesia"}}]}))
    (tmp_path / "data" / "rows.csv").write_text("place,mag\n\"85 km WNW of San Juan, Peru\",5.9\n")
    pool, _derived, names = filecheck.vouched(tmp_path)
    assert names["data_files"] == ["data/feed.geojson", "data/rows.csv"]
    for text in ("148 km WSW of Tual", "85 km WNW of San Juan", "magnitude 6.6"):
        (figure,), _skipped = figures_in(text, "Slide 1")
        assert filecheck.judge(figure, pool, _derived)["status"] == "found", text
    (figure,), _skipped = figures_in("149 km WSW of Tual", "Slide 1")
    assert filecheck.judge(figure, pool, _derived)["status"] == "not_found"


@pytest.mark.parametrize("text", ["11–18 November 2026 (UTC)", "Feed generated 18 Nov 2026, 20:14 UTC.",
                                  "November 21-28, 2026", "21 – 28 Nov"])
def test_a_run_of_days_is_a_date_and_not_a_figure(text):
    assert figures_in(text, "Slide 1")[0] == []
