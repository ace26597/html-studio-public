"""Office documents (CONTRACT.md, "Office documents")."""

import json
import zipfile

import pytest

from studio2_server import office, tools

openpyxl = pytest.importorskip("openpyxl")
needs_office = pytest.mark.skipif(office.reason() is not None, reason="LibreOffice is not installed")


def workbook(path, with_values):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales & costs"
    ws.append(["Region", "Q1", "Q2", "Total", "Share", "Label"])
    rows = [("North", 120, 135), ("South", 90, 110)]
    for r, (name, a, b) in enumerate(rows, start=2):
        ws.append([name, a, b, a + b if with_values else f"=B{r}+C{r}",
                   (a + b) / 455 if with_values else f"=D{r}/D$4", "big" if with_values and a + b > 220 else
                   ("small" if with_values else f'=IF(D{r}>220,"big","small")')])
    ws.append(["All", 210 if with_values else "=SUM(B2:B3)", 245 if with_values else "=SUM(C2:C3)",
               455 if with_values else "=SUM(D2:D3)", None if with_values else "=D4/0", None])
    ws["A1"].font = openpyxl.styles.Font(bold=True)
    wb.save(path)


def test_formula_results_are_written_next_to_the_formulas(tmp_path):
    original, computed = tmp_path / "book.xlsx", tmp_path / "computed.xlsx"
    workbook(original, with_values=False)
    workbook(computed, with_values=True)
    assert office.stamp_values(original, computed) == 9          # the division by zero has no result to store
    stored = openpyxl.load_workbook(original, data_only=True).active
    assert [stored["D2"].value, stored["D4"].value, stored["F2"].value, stored["F3"].value] == [255, 455, "big", "small"]
    assert stored["E2"].value == pytest.approx(255 / 455)
    kept = openpyxl.load_workbook(original).active
    assert kept["D2"].value == "=B2+C2" and kept["F2"].value == '=IF(D2>220,"big","small")' and kept["A1"].font.b
    with zipfile.ZipFile(original) as archive:
        assert archive.testzip() is None


def test_a_sheet_is_read_with_formulas_values_and_errors(tmp_path):
    original, computed = tmp_path / "book.xlsx", tmp_path / "computed.xlsx"
    workbook(original, with_values=False)
    workbook(computed, with_values=True)
    sheet = office.read_workbook(original, computed)[0]
    assert sheet["name"] == "Sales & costs" and sheet["formulas"] == 10
    assert sheet["cells"][1][3] == {"t": "255", "f": "=B2+C2", "n": 1}
    assert sheet["cells"][0][0] == {"t": "Region", "b": 1}
    assert sheet["errors"] == [{"cell": "E4", "formula": "=D4/0", "value": "no result"}]


def test_the_viewer_is_one_file_with_the_data_inside(tmp_path):
    page = office.write_viewer(tmp_path, {"name": "a</script>.pptx", "kind": "presentation", "label": "PowerPoint",
                                          "file": "../a.pptx", "bytes": 10, "pages": ["page-1.png"], "page_count": 1})
    text = page.read_text()
    assert "/*DATA*/null" not in text and '"page-1.png"' in text and "</script>.pptx" not in text
    assert "http" not in text.split("<script>")[1]                   # nothing is fetched from anywhere


async def test_the_tool_refuses_what_it_cannot_render(studio, project):
    workspace = studio.store.workspace(project["id"])
    (workspace / "notes.txt").write_text("x")
    answer = await tools.call(studio, project["id"], "render_document", {"path": "notes.txt"})
    assert answer.get("is_error") and "PowerPoint, Word, Excel and PDF" in answer["content"][0]["text"]
    missing = await tools.call(studio, project["id"], "render_document", {"path": "files/none.pptx"})
    assert missing.get("is_error") and "no file" in missing["content"][0]["text"]
    outside = await tools.call(studio, project["id"], "render_document", {"path": "../../x.pptx"})
    assert outside.get("is_error")


@needs_office
async def test_a_workbook_is_calculated_checked_and_given_a_preview(studio, project):
    workspace = studio.store.workspace(project["id"])
    (workspace / "files").mkdir()
    workbook(workspace / "files/book.xlsx", with_values=False)
    answer = await tools.call(studio, project["id"], "render_document", {"path": "files/book.xlsx"})
    assert not answer.get("is_error"), answer
    result = json.loads(answer["content"][0]["text"])
    sheet = result["sheets"][0]
    assert sheet["formulas"] == 10 and [e["cell"] for e in sheet["errors"]] == ["E4"]
    assert sheet["start"][1].startswith("North | 120 | 135 | 255")
    assert result["preview"] == "files/book.xlsx.preview/index.html" and (workspace / result["preview"]).is_file()
    assert openpyxl.load_workbook(workspace / "files/book.xlsx", data_only=True).active["D4"].value == 455


async def test_a_project_file_can_be_sent_as_a_download(client, studio, project):
    import httpx
    workspace = studio.store.workspace(project["id"])
    (workspace / "files").mkdir()
    (workspace / "files/Q3 deck.pptx").write_bytes(b"PK")
    opened = await studio.open_preview(studio.store.get(project["id"]))
    base = f"http://127.0.0.1:{opened['port']}"
    async with httpx.AsyncClient() as web:
        plain = await web.get(f"{base}/__studio/files/files/Q3%20deck.pptx")
        assert plain.status_code == 200 and "content-disposition" not in plain.headers
        assert plain.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.presentationml")
        saved = await web.get(f"{base}/__studio/files/files/Q3%20deck.pptx?download=1")
        assert saved.headers["content-disposition"] == 'attachment; filename="Q3 deck.pptx"'


def test_an_empty_styled_cell_before_a_formula_is_left_alone(tmp_path):
    """The first real workbook had styled empty cells next to formulas; stamping must not merge them."""
    original, computed = tmp_path / "book.xlsx", tmp_path / "computed.xlsx"
    for path, formula in ((original, True), (computed, False)):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"], ws["B1"] = 5, 7
        ws["C1"].font = openpyxl.styles.Font(bold=True)              # styled, empty: written as <c r="C1" s="1"/>
        ws["D1"] = "=A1+B1" if formula else 12
        ws["E1"] = ""
        ws["F1"] = '=IF(A1>1,"a<b & c","")' if formula else "a<b & c"
        wb.save(path)
    assert office.stamp_values(original, computed) == 2
    for data_only, expected in ((True, [5, 7, None, 12, None, "a<b & c"]), (False, [5, 7, None, "=A1+B1", None, '=IF(A1>1,"a<b & c","")'])):
        row = [c.value for c in openpyxl.load_workbook(original, data_only=data_only).active[1]]
        assert row[:4] == expected[:4] and row[5] == expected[5]
    assert openpyxl.load_workbook(original).active["C1"].font.b


@pytest.mark.parametrize("value, number_format, text", [
    (1472.985, "#,##0.0", "1,473.0"), (0.466402, "0.0%", "46.6%"), (-0.285, "0.0%;[Red]-0.0%", "-28.5%"),
    (1234567, "#,##0", "1,234,567"), (931800000, '$#,##0.0,,"M"', "$931.8M"), (-301.679, "#,##0.0;(#,##0.0)", "(301.7)"),
    (12771, "General", "12771"), (0.1 + 0.2, "General", "0.3"), (5, "0.00", "5.00"), (True, "General", "TRUE"),
    (None, "0.0", ""), ("n/a", "0.0%", "n/a"), (1500, '[$€-407]#,##0', "€1,500"), (-0.0001, "0.0", "0.0"),
])
def test_values_are_shown_the_way_a_spreadsheet_shows_them(value, number_format, text):
    assert office.shown(value, number_format) == text


def test_dates_follow_their_format():
    import datetime
    day = datetime.datetime(2026, 9, 30)
    assert office.shown(day, "yyyy-mm-dd") == "2026-09-30"
    assert office.shown(day, "d mmm yyyy") == "30 Sep 2026"
    assert office.shown(day, "General") == "2026-09-30 00:00:00"
