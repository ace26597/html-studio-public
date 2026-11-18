"""Converting a finished document to another format (CONTRACT.md, "Diagrams and conversions":
`convert_document`). Real conversions with the LibreOffice and pandoc installed here; each result
is read back. Tests that need a program skip with a reason when it is absent."""

import json
import re
import subprocess
import zipfile

import pytest

from studio2_server import convert, diagram, office, tools
from studio2_server.look import Looker

needs_office = pytest.mark.skipif(office.find_soffice() is None, reason="LibreOffice is not installed")
needs_pandoc = pytest.mark.skipif(convert.find_pandoc() is None, reason="pandoc is not installed")
needs_browser = pytest.mark.skipif(diagram.reason(Looker()) is not None, reason="no cached Chromium for diagrams")

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
ODD_FONT = "Studio Absent Serif"      # a font no computer has, to see the substitution reported


def run(text: str) -> str:
    return f'<w:p><w:r><w:rPr><w:rFonts w:ascii="{ODD_FONT}" w:hAnsi="{ODD_FONT}"/></w:rPr><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def make_docx(path, tracked: bool = False):
    """A small Word file written by hand: two paragraphs, a table, optionally a tracked insertion."""
    cell = lambda t: f"<w:tc><w:p><w:r><w:t>{t}</w:t></w:r></w:p></w:tc>"  # noqa: E731
    table = "<w:tbl><w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/></w:tblPr>" + "".join(
        f"<w:tr>{cell(a)}{cell(b)}</w:tr>" for a, b in (("Region", "Sales"), ("North", "120"), ("South", "80"))) + "</w:tbl>"
    insert = ('<w:p><w:ins w:id="1" w:author="A" w:date="2026-11-01T00:00:00Z"><w:r><w:t>Added later.</w:t></w:r></w:ins></w:p>'
              if tracked else "")
    body = f'<w:document {W}><w:body>{run("Quarterly note")}{run("Revenue rose 12% to 4.2 billion.")}{table}{insert}<w:sectPr/></w:body></w:document>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + body)
    return path


def make_xlsx(path):
    import openpyxl
    book = openpyxl.Workbook()
    first = book.active
    first.title = "Inputs"
    first.append(["a", "b", "sum"])
    first.append([1, 2, "=A2+B2"])
    second = book.create_sheet("Second sheet")
    second.append(["city", "name"])
    second.append(["Zürich", "Café"])
    book.save(path)
    return path


MARKDOWN = """# Summary

Revenue rose **12%** to $4.2 billion.

| Region | Sales |
|---|---|
| North | 120 |
| South | 80 |

```mermaid
flowchart LR
  A[Plan] --> B[Build]
```

Closing words.
"""


class _Studio:
    def __init__(self, looker):
        self.looker = looker


@pytest.fixture
async def studio():
    looker = Looker()
    yield _Studio(looker)
    await looker.close()


def zip_text(path, name) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode("utf-8")


def pdf_text(path) -> tuple[int, str]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return len(reader.pages), " ".join(page.extract_text() or "" for page in reader.pages)


# ---- LibreOffice ------------------------------------------------------------------------------
@needs_office
async def test_word_to_rtf_odt_and_pdf(studio, tmp_path):
    make_docx(tmp_path / "note.docx", tracked=True)
    rtf = await convert.convert(studio, tmp_path, "note.docx", "rtf")
    assert rtf["path"] == "note.rtf" and rtf["opens"] and rtf["tool"] == "LibreOffice"
    assert rtf["version"].startswith("LibreOffice ") and rtf["size"] > 0
    text = (tmp_path / "note.rtf").read_text("latin-1")
    assert text.startswith("{\\rtf") and "Revenue rose" in text and "North" in text and "\\trowd" in text   # a real table
    assert any("tracked changes" in note and "not checked" in note for note in rtf["not_carried"])
    assert "render_document" in rtf["next"] and "check_file" not in rtf["next"]       # check_file does not read RTF

    odt = await convert.convert(studio, tmp_path, "note.docx", ".odt")
    content = zip_text(tmp_path / "note.odt", "content.xml")
    assert odt["opens"] and "Revenue rose" in content and "<table:table " in content and "South" in content

    pdf = await convert.convert(studio, tmp_path, "note.docx", "pdf")
    pages, words = pdf_text(tmp_path / "note.pdf")
    assert pdf["opens"] and pdf["pages"] == pages >= 1 and "Revenue rose" in words
    assert any(ODD_FONT in note and "replaced" in note for note in pdf["not_carried"]), pdf["not_carried"]
    assert "check_file on note.pdf" in pdf["next"]


@needs_office
async def test_odt_and_rtf_back_to_word(studio, tmp_path):
    make_docx(tmp_path / "note.docx")
    await convert.convert(studio, tmp_path, "note.docx", "odt")
    await convert.convert(studio, tmp_path, "note.docx", "rtf")
    (tmp_path / "back").mkdir()
    for name in ("note.odt", "note.rtf"):
        (tmp_path / "back" / name).write_bytes((tmp_path / name).read_bytes())
        result = await convert.convert(studio, tmp_path, f"back/{name}", "docx")
        assert result["opens"] and result["path"] == "back/note.docx"
        assert "Revenue rose" in zip_text(tmp_path / "back" / "note.docx", "word/document.xml")
        (tmp_path / "back" / "note.docx").unlink()


@needs_office
async def test_workbook_to_ods_and_one_named_sheet_as_csv(studio, tmp_path):
    make_xlsx(tmp_path / "model.xlsx")
    ods = await convert.convert(studio, tmp_path, "model.xlsx", "ods")
    assert ods["opens"] and "Second sheet" in zip_text(tmp_path / "model.ods", "content.xml")
    with pytest.raises(convert.ConvertError, match="Inputs, Second sheet"):
        await convert.convert(studio, tmp_path, "model.xlsx", "csv")
    with pytest.raises(convert.ConvertError, match="no sheet named"):
        await convert.convert(studio, tmp_path, "model.xlsx", "csv", sheet="Third")
    second = await convert.convert(studio, tmp_path, "model.xlsx", "csv", sheet="Second sheet")
    assert second["path"] == "model-Second-sheet.csv" and second["opens"]
    assert (tmp_path / second["path"]).read_text("utf-8").splitlines() == ["city,name", "Zürich,Café"]
    assert any("Second sheet" in note and "without formulas" in note for note in second["not_carried"])
    first = await convert.convert(studio, tmp_path, "model.xlsx", "csv", sheet="Inputs")
    assert (tmp_path / first["path"]).read_text("utf-8").splitlines()[1] == "1,2,3"     # the formula, calculated


@needs_office
@needs_pandoc
async def test_deck_to_pdf_and_odp(studio, tmp_path):
    slides = "# First slide\n\nOne point.\n\n# Second slide\n\nAnother point.\n"
    subprocess.run([convert.find_pandoc(), "-f", "markdown", "-o", str(tmp_path / "deck.pptx"), "-"],
                   input=slides, text=True, check=True, timeout=60)
    pdf = await convert.convert(studio, tmp_path, "deck.pptx", "pdf")
    pages, words = pdf_text(tmp_path / "deck.pdf")
    assert pdf["opens"] and pages == 2 and "Second slide" in words
    odp = await convert.convert(studio, tmp_path, "deck.pptx", "odp")
    assert odp["opens"] and "Second slide" in zip_text(tmp_path / "deck.odp", "content.xml")


# ---- pandoc -----------------------------------------------------------------------------------
@needs_pandoc
@needs_browser
async def test_markdown_to_each_format_with_its_diagram_drawn(studio, tmp_path):
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "report.md").write_text(MARKDOWN)
    docx = await convert.convert(studio, tmp_path, "files/report.md", "docx")
    assert docx["path"] == "files/report.docx" and docx["opens"] and docx["tool"] == "pandoc"
    assert docx["version"].startswith("pandoc ") and docx["diagrams"] == [
        {"n": 1, "picture": "files/diagrams/report-diagram-1.png"}]
    with zipfile.ZipFile(tmp_path / "files" / "report.docx") as z:
        body = z.read("word/document.xml").decode()
        assert "Revenue rose" in body and "<w:tbl>" in body and "flowchart LR" not in body
        assert any(name.startswith("word/media/") for name in z.namelist())        # the diagram, as a picture
    assert any("pandoc's plain defaults" in note for note in docx["not_carried"])
    assert "check_file on files/report.docx" in docx["next"]

    odt = await convert.convert(studio, tmp_path, "files/report.md", "odt")
    assert odt["opens"] and "Revenue rose" in zip_text(tmp_path / "files" / "report.odt", "content.xml")
    rtf = await convert.convert(studio, tmp_path, "files/report.md", "rtf")
    text = (tmp_path / "files" / "report.rtf").read_text("latin-1")
    assert rtf["opens"] and text.startswith("{\\rtf") and "Revenue rose" in text      # a whole RTF file, not a fragment
    epub = await convert.convert(studio, tmp_path, "files/report.md", "epub")
    with zipfile.ZipFile(tmp_path / "files" / "report.epub") as z:
        assert "META-INF/container.xml" in z.namelist()
        assert any("Revenue rose" in z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".xhtml"))
    assert any("no title" in note for note in epub["not_carried"])
    html = await convert.convert(studio, tmp_path, "files/report.md", "html")
    page = (tmp_path / "files" / "report.html").read_text()
    assert html["opens"] and page.lstrip().lower().startswith("<!doctype html") and "Revenue rose" in page
    assert "data:image/svg+xml" in page and "<script" not in page.lower() and "look_at_page" in html["next"]


@needs_pandoc
@needs_browser
async def test_a_broken_diagram_block_is_left_as_code_and_said(studio, tmp_path):
    (tmp_path / "r.md").write_text("Intro.\n\n```mermaid\nflowchart LR\n  A --> (B\n```\n")
    result = await convert.convert(studio, tmp_path, "r.md", "docx")
    assert result["opens"] and result["diagrams"][0]["line"] == 5
    assert any("did not draw" in note and "line 5" in note for note in result["not_carried"])
    assert "(B" in zip_text(tmp_path / "r.docx", "word/document.xml")


@needs_pandoc
async def test_markdown_follows_a_reference_documents_styles(studio, tmp_path):
    (tmp_path / "r.md").write_text("# Title\n\nBody text.\n")
    default = subprocess.run([convert.find_pandoc(), "--print-default-data-file", "reference.docx"],
                             capture_output=True, check=True, timeout=60).stdout
    (tmp_path / "plain.docx").write_bytes(default)
    with zipfile.ZipFile(tmp_path / "plain.docx") as source, zipfile.ZipFile(tmp_path / "house.docx", "w") as out:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/styles.xml":
                data = data.replace(b"</w:styles>", b'<w:style w:type="paragraph" w:customStyle="1" w:styleId="HouseMark">'
                                                    b'<w:name w:val="House Mark"/></w:style></w:styles>')
            out.writestr(item, data)
    result = await convert.convert(studio, tmp_path, "r.md", "docx", reference="house.docx")
    assert "House Mark" in zip_text(tmp_path / "r.docx", "word/styles.xml")
    assert not any("plain defaults" in note for note in result["not_carried"])
    with pytest.raises(convert.ConvertError, match="reference"):
        await convert.convert(studio, tmp_path, "r.md", "odt", reference="house.docx")


@needs_pandoc
async def test_word_to_markdown(studio, tmp_path):
    make_docx(tmp_path / "note.docx", tracked=True)
    result = await convert.convert(studio, tmp_path, "note.docx", "md")
    text = (tmp_path / "note.md").read_text()
    assert result["opens"] and "Revenue rose 12% to 4.2 billion." in text and "Added later." in text
    assert re.search(r"\|\s*North\s*\|\s*120\s*\|", text)
    assert any("accepts them" in note for note in result["not_carried"])


# ---- where results go, and what is refused ---------------------------------------------------
@needs_pandoc
async def test_an_existing_file_is_kept_unless_replace(studio, tmp_path):
    (tmp_path / "r.md").write_text("Text.\n")
    (tmp_path / "r.docx").write_bytes(b"hand made")
    with pytest.raises(convert.ConvertError, match="already exists.*replace: true"):
        await convert.convert(studio, tmp_path, "r.md", "docx")
    assert (tmp_path / "r.docx").read_bytes() == b"hand made"
    result = await convert.convert(studio, tmp_path, "r.md", "docx", replace=True)
    assert result["replaced"] is True and result["opens"]


@needs_pandoc
async def test_a_template_file_is_converted_into_files(studio, tmp_path):
    (tmp_path / "template").mkdir()
    (tmp_path / "template" / "guide.md").write_text("Guide.\n")
    result = await convert.convert(studio, tmp_path, "template/guide.md", "docx")
    assert result["path"] == "files/guide.docx" and not (tmp_path / "template" / "guide.docx").exists()


async def test_paths_and_pairs_are_refused(studio, tmp_path):
    (tmp_path.parent / "outside.md").write_text("x")
    for bad in ("../outside.md", "a/../../outside.md", ".git/config"):
        with pytest.raises(convert.ConvertError, match="inside the project"):
            await convert.convert(studio, tmp_path, bad, "docx")
    (tmp_path / "a.md").write_text("x")
    (tmp_path / "pic.png").write_bytes(b"x")
    with pytest.raises(convert.ConvertError, match="converts to .docx, .epub, .html, .odt, .rtf here, not .pptx"):
        await convert.convert(studio, tmp_path, "a.md", "pptx")
    with pytest.raises(convert.ConvertError, match="cannot be converted here"):
        await convert.convert(studio, tmp_path, "pic.png", "pdf")
    with pytest.raises(convert.ConvertError, match="no file"):
        await convert.convert(studio, tmp_path, "missing.md", "docx")


async def test_a_missing_program_says_what_to_install(studio, tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("x")
    make_xlsx(tmp_path / "m.xlsx")
    monkeypatch.setattr(convert, "find_pandoc", lambda: None)
    with pytest.raises(convert.ConvertError, match="pandoc is not installed.*brew install pandoc"):
        await convert.convert(studio, tmp_path, "a.md", "docx")
    monkeypatch.setattr(office, "find_soffice", lambda: None)
    with pytest.raises(convert.ConvertError, match="LibreOffice is not installed.*brew install --cask libreoffice"):
        await convert.convert(studio, tmp_path, "m.xlsx", "ods")
    assert "pandoc" in convert.reason() and "LibreOffice" in convert.reason()


async def test_the_tool_answers_through_the_studio(studio, tmp_path):
    studio.store = type("S", (), {"workspace": lambda _self, _pid: tmp_path})()
    refused = await tools.call(studio, "p1", "convert_document", {"path": "../x.md", "to": "docx"})
    assert refused["is_error"] and "inside the project" in refused["content"][0]["text"]
    if convert.find_pandoc():
        (tmp_path / "a.md").write_text("Hello.\n")
        answer = await tools.call(studio, "p1", "convert_document", {"path": "a.md", "to": "html"})
        assert json.loads(answer["content"][0]["text"])["path"] == "a.html"
