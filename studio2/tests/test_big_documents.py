"""Very large documents (CONTRACT.md, "Very large documents"): the ledger, leftover placeholders,
two figures for one label, and every cap a 104-slide deck or a 50-sheet workbook reaches.

The large files are built here from XML parts and openpyxl, with a ledger, three planted wrong
figures and two leftover placeholders each; the checks must find exactly those. Times are printed
(run with -s to see them)."""

import itertools
import json
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from office_parts import make_real_pptx
from studio2_server import filecheck, office, tools
from studio2_server.filecheck import check_file, conflicts_in, figures_in, placeholders_in
from test_filecheck import _shape, _table, _wp, by_text, make_docx, make_pptx, vouching_project

openpyxl = pytest.importorskip("openpyxl")
needs_office = pytest.mark.skipif(office.reason() is not None, reason="LibreOffice is not installed")
LIBRARY = Path(__file__).resolve().parents[1] / "library"


def ledger(root, facts):
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "facts.json").write_text(json.dumps({"facts": facts}))


def base_facts():
    return [
        {"key": "revenue_2025", "label": "Revenue", "value": 4218950, "unit": "USD", "period": "FY2025",
         "source": "s1", "obtained": "quoted", "quote": "revenue was $4,218,950"},
        {"key": "margin_2025", "label": "Margin", "value": 12.4, "unit": "%", "period": "FY2025",
         "source": "s1", "obtained": "quoted"},
        {"key": "units_north", "value": 1520, "unit": "units", "source": "data/sales.csv", "obtained": "data"},
        {"key": "units_total", "value": 2500, "unit": "units", "source": "data/sales.csv", "obtained": "data"},
        {"key": "revenue_per_unit", "value": 1687.58, "unit": "USD", "obtained": "computed",
         "from": ["revenue_2025", "units_total"], "formula": "revenue_2025 / units_total"},
        {"key": "target_margin", "value": 18, "unit": "%", "source": "person", "obtained": "given"},
    ]


# -- the ledger ------------------------------------------------------------------------------------
def test_a_figure_in_the_ledger_is_reported_by_its_key(tmp_path):
    vouching_project(tmp_path)
    ledger(tmp_path, base_facts())
    make_docx(tmp_path / "memo.docx", _wp("Revenue was $4.2 million on 2,500 units, or $1,687.58 a unit.")
              + _wp("North sold 1,520 units; the margin was 12.4% against a target of 18%.")
              + _wp("Costs were $7.7 million."))
    result = check_file(tmp_path, "memo.docx")
    items = by_text(result)
    assert (items["$4.2 million"]["via"], items["$4.2 million"]["ref"], items["$4.2 million"]["match"]) == (
        "ledger", "revenue_2025", "rounded")
    assert (items["2,500"]["via"], items["2,500"]["ref"]) == ("ledger", "units_total")
    assert items["$1,687.58"]["ref"] == "revenue_per_unit" and items["$1,687.58"]["note"].startswith(
        "computed in the ledger: revenue_2025 / units_total")
    assert items["18%"]["ref"] == "target_margin" and "given by the person" in items["18%"]["note"]
    assert items["$7.7 million"]["status"] == "not_found"
    assert result["summary"]["ledger"] == 6 and result["summary"]["not_found"] == 1
    assert result["ledger"] == {"file": "data/facts.json", "facts": 6, "vouching": 6, "computed": 1, "given": 1,
                                "problems": [], "problems_total": 0, "error": None}
    assert "data/facts.json" not in result["vouched"]["data_files"]       # read as the ledger, not as data


def test_a_fact_with_a_problem_vouches_for_nothing(tmp_path):
    vouching_project(tmp_path)
    facts = base_facts() + [
        {"key": "revenue_2024", "value": 3900000, "source": "s1", "obtained": "quoted"},       # not in s1
        {"key": "revenue_2023", "value": 3100000, "source": "s9", "obtained": "quoted"},       # no such source
        {"key": "units_south", "value": 990, "source": "data/sales.csv", "obtained": "data"},  # the file says 980
        {"key": "growth", "value": 9.9, "obtained": "computed", "from": ["revenue_2024", "revenue_2025"],
         "formula": "(revenue_2025 / revenue_2024 - 1) * 100"},                               # input has a problem
        {"key": "per_unit_wrong", "value": 1700, "obtained": "computed", "from": ["revenue_2025", "units_total"],
         "formula": "revenue_2025 / units_total"},                                             # gives 1687.58
        {"key": "sneaky", "value": 5, "obtained": "computed", "from": ["units_total"],
         "formula": "__import__('os').getpid()"},
        {"key": "loop_a", "value": 1, "obtained": "computed", "from": ["loop_b"], "formula": "loop_b"},
        {"key": "loop_b", "value": 1, "obtained": "computed", "from": ["loop_a"], "formula": "loop_a"},
        {"key": "units_north", "value": 1520, "source": "data/sales.csv", "obtained": "data"},  # twice
        {"key": "Bad Key", "value": 1, "obtained": "given", "source": "person"},
        {"key": "no_number", "value": "about a third", "obtained": "given", "source": "person"},
        {"key": "no_way", "value": 4, "source": "s1"},
    ]
    ledger(tmp_path, facts)
    make_docx(tmp_path / "memo.docx", _wp("Revenue 2024 was 3,900,000 and 990 units went south; "
                                          "1,700 a unit; growth 9.9%."))
    result = check_file(tmp_path, "memo.docx", save=False)
    why = {p["key"]: p["why"] for p in result["ledger"]["problems"]}
    assert why["revenue_2024"] == "3900000 is not in the saved text of s1."
    assert why["revenue_2023"] == "s9 is not a saved source."
    assert why["units_south"] == "990 is not in data/sales.csv."
    assert why["growth"] == "its input revenue_2024 has a problem."
    assert why["per_unit_wrong"] == "the formula gives 1687.58, not 1700."
    assert "something other than keys" in why["sneaky"]
    assert "depends on itself" in why["loop_a"] or "depends on itself" in why["loop_b"]
    assert why["units_north"] == "the same key is in the ledger twice."
    assert "lower-case name" in why["Bad Key"] and why["no_number"] == "its value is not a number."
    assert why["no_way"].startswith("obtained is not")
    assert result["ledger"]["problems_total"] == len(why) == 12
    assert {i["text"]: i["status"] for i in result["items"]} == {
        "3,900,000": "not_found", "990": "not_found", "1,700": "not_found", "9.9%": "not_found"}
    answer = filecheck.compact(result)
    assert "has 12 problems" in answer["next"] and "vouches for nothing" in answer["next"]


def test_a_ledger_of_another_shape_is_read_as_data_and_said(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "facts.json").write_text(json.dumps({"revenue": 4218950}))
    make_docx(tmp_path / "memo.docx", _wp("Revenue was $4,218,950."))
    result = check_file(tmp_path, "memo.docx", save=False)
    assert result["ledger"]["error"].endswith("read as an ordinary data file.")
    assert result["items"][0]["via"] == "data" and result["items"][0]["ref"] == "data/facts.json, revenue"
    assert check_file(tmp_path, "memo.docx", save=False)["summary"]["ledger"] == 0
    (tmp_path / "data" / "facts.json").unlink()
    assert check_file(tmp_path, "memo.docx", save=False)["ledger"] is None


# -- leftover placeholders -------------------------------------------------------------------------
@pytest.mark.parametrize("text, found", [
    ("Click to add title", ["Click to add title"]),
    ("Click icon to add picture", ["Click icon to add picture"]),
    ("Lorem ipsum dolor sit amet", ["Lorem ipsum dolor sit amet"]),
    ("Dear {{client_name}}, from ${sender} to {recipient} and <<company>>", ["{{client_name}}", "${sender}",
                                                                             "{recipient}", "<<company>>"]),
    ("Revenue [insert figure] as said [Source]; see [1] and [12]", ["[insert figure]", "[Source]"]),
    ("Your logo here", ["Your logo here"]),
    ("Launch date: TBD, owner TBC, FIXME later", ["TBD", "TBC", "FIXME"]),
    ("TODO: write this", ["TODO:"]),
    ("Revenue of $X.X million, XX% up, code XXX", ["$X.X million", "XX%", "XXX"]),
    # Not placeholders: a subject that uses the words.
    ("Our TODO app keeps your to-do list; your journey starts here", []),
    ("Set {a b} and x = {1, 2}; the todo list", []),
    ("Mexico grew 12% (XL size)", []),
])
def test_leftover_placeholders_are_found_and_a_real_subject_is_not(text, found):
    assert [p["text"] for p in placeholders_in(text, "here")] == found


def test_an_empty_placeholder_on_a_slide_is_a_leftover(tmp_path):
    def empty(n, kind):
        return (f'<p:sp><p:nvSpPr><p:cNvPr id="{n}" name="Text 0"/><p:cNvSpPr/><p:nvPr><p:ph type="{kind}"/>'
                '</p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>')
    make_pptx(tmp_path / "deck.pptx", [
        [empty(2, "title"), _shape(3, "Content 2", [["Click to add text"]], "body")],
        [_shape(2, "Title 1", [["A full slide"]], "title"), empty(3, "body"), empty(4, "sldNum"), empty(5, "dt")]])
    result = check_file(tmp_path, "deck.pptx", save=False)
    assert [(p["text"], p["where"]) for p in result["placeholders"]] == [
        ("(empty title)", "Slide 1, title"), ("Click to add text", "Slide 1, Content 2"),
        ("(empty body placeholder)", "Slide 2, body placeholder")]
    assert result["summary"]["placeholders"] == 3
    assert "3 leftover placeholder texts" in filecheck.compact(result)["next"]


# -- two figures for one label ---------------------------------------------------------------------
def test_two_figures_for_one_label_are_a_conflict_and_a_rounding_is_not():
    figures = []
    for text, where in [("Revenue 2025: $4.2m", "Slide 3"), ("Revenue 2025: $4,218,950", "Slide 9"),
                        ("revenue 2025 = $4.3m", "Slide 41"), ("Revenue: $9.9m", "Slide 5"),
                        ("Revenue: $1.1m", "Slide 6"), ("North share: 12%", "Slide 7"),
                        ("North share: $12m", "Slide 8"), ("North share: 13%", "Slide 9")]:
        figures += figures_in(text, where)[0]
    assert conflicts_in(figures) == [
        {"label": "Revenue 2025", "figures": [{"text": "$4.2m", "where": "Slide 3"},
                                              {"text": "$4.3m", "where": "Slide 41"}]},
        {"label": "North share", "figures": [{"text": "12%", "where": "Slide 7"}, {"text": "13%", "where": "Slide 9"}]}]


def test_a_range_written_in_one_place_is_not_a_conflict():
    """Found on a real 40-slide deck: a table cell "70% to 158%" was reported as two figures that disagree."""
    figures = figures_in("Growth in the new states: 70% to 158%", "Slide 37, table row 5, column 3")[0]
    assert len(figures) == 2 and conflicts_in(figures) == []
    figures += figures_in("Growth in the new states: 70%", "Slide 12")[0]
    assert conflicts_in(figures) == []
    figures += figures_in("Growth in the new states: 90%", "Slide 20")[0]
    assert [c["label"] for c in conflicts_in(figures)] == ["Growth in the new states"]


def test_a_table_cell_is_labelled_by_its_row_and_column_and_a_workbook_is_not_compared(tmp_path):
    make_pptx(tmp_path / "deck.pptx", [
        [_table(2, [["Region", "2025"], ["North", "1,520"]])],
        [_shape(2, "Content", [["North 2025: 1,620"]])]])
    result = check_file(tmp_path, "deck.pptx", save=False)
    assert result["conflicts"] == [{"label": "North 2025", "figures": [
        {"text": "1,520", "where": "Slide 1, table row 2, column 2"}, {"text": "1,620", "where": "Slide 2, Content"}]}]
    assert result["by_part"] == [{"part": "Slide 1", "not_found": 1, "placeholders": 0, "conflicts": 1},
                                 {"part": "Slide 2", "not_found": 1, "placeholders": 0, "conflicts": 1}]
    book = openpyxl.Workbook()
    book.active.append(["Total revenue: 4,000", "Total revenue: 5,000"])
    book.save(tmp_path / "book.xlsx")
    assert check_file(tmp_path, "book.xlsx", save=False)["conflicts"] == []


# -- caps ------------------------------------------------------------------------------------------
def test_past_the_item_cap_every_not_found_figure_is_still_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(filecheck, "MAX_ITEMS", 3)
    vouching_project(tmp_path)
    make_docx(tmp_path / "memo.docx", _wp("1,520 and 1,520 and 1,520 and 1,520 and 1,777 and 1,520 and 1,888"))
    result = check_file(tmp_path, "memo.docx", save=False)
    assert result["summary"]["figures"] == 7 and result["items_cut"] == 2
    assert [i["text"] for i in result["items"]] == ["1,520", "1,520", "1,520", "1,777", "1,888"]


def test_an_answer_with_many_problems_is_cut_and_says_where_the_rest_are(tmp_path):
    slides = [[_shape(2, "Title 1", [["Lorem ipsum dolor"]], "title"),
               _shape(3, "Body", [[f"Sales {1000 + n:,} and {2000 + n:,} and {3000 + n:,}"]])] for n in range(150)]
    make_pptx(tmp_path / "deck.pptx", slides)
    result = check_file(tmp_path, "deck.pptx")
    answer = filecheck.compact(result)
    assert result["summary"]["not_found"] == 450 and len(answer["not_found"]) == 40
    assert len(answer["placeholders"]) == 30 and len(answer["by_part"]) == 120 and answer["lists_cut"] is True
    assert "the full lists are in checks/deck.pptx.json" in answer["next"]
    assert len(json.dumps(answer, ensure_ascii=False)) < tools.RESULT_CAP


def test_page_ranges_are_read_and_bounded():
    assert office.page_range(None, 104, 60) == (1, 60, None)
    assert office.page_range("61-", 104, 60) == (61, 104, None)
    assert office.page_range("41 - 60", 104, 60) == (41, 60, None)
    assert office.page_range("7", 104, 60) == (7, 7, None)
    first, last, note = office.page_range("1-104", 104, 60)
    assert (first, last) == (1, 60) and "cut to 1-60" in note
    for bad, says in (("0-3", "a range"), ("x", "a range"), ("9-3", "ends before"), ("200", "has 104 pages")):
        with pytest.raises(office.OfficeError, match=says):
            office.page_range(bad, 104, 60)
    assert office.ranges([1, 2, 3, 7, 9, 10]) == ["1-3", "7", "9-10"]
    assert office._not_drawn(list(range(61, 200)), 199).endswith('pages: "61-120".')


def test_a_workbook_answer_stays_readable_at_fifty_sheets():
    sheets = [{"name": f"Sheet {n}", "rows": 30, "columns": 10, "cut": False, "formulas": 40, "unchecked": 0,
               "charts": 0, "errors": [{"cell": f"B{r}", "formula": "=1/0" + "+A1" * 60, "value": "#DIV/0!"}
                                       for r in range(40)], "error_count": 40,
               "cells": [[{"t": f"value {r}-{c} long text"} for c in range(10)] for r in range(30)]}
              for n in range(50)]
    result = office.workbook_result(sheets, 0, [])
    assert result["error_total"] == 2000 and result["errors_listed"] == 100
    assert [len(s["errors"]) for s in result["sheets"][:4]] == [30, 30, 30, 10]
    cut = [s["start_cut"] for s in result["sheets"]]
    assert cut[0] is False and cut[-1] is True and cut == sorted(cut)
    assert "start is given for the first" in result["next"] and "errors lists 100 of the 2,000" in result["next"]
    assert len(json.dumps(result, ensure_ascii=False)) < tools.RESULT_CAP


# -- a 104-slide deck and a 50-sheet workbook ------------------------------------------------------
REGIONS = [f"Area {a}{b}" for a, b in itertools.islice(itertools.product("ABCDEFGHIJ", repeat=2), 100)]


def sales(i: int) -> int:
    return 104_000 + 1_237 * i


def large_project(root: Path) -> list[dict]:
    """A project with a saved source, two data files and a ledger of 206 facts (100 region sales
    from data, their 100 shares computed in the ledger, and the base facts)."""
    vouching_project(root)
    (root / "data" / "regions.csv").write_text("region,sales\n" + "".join(
        f"{name},{sales(i)}\n" for i, name in enumerate(REGIONS)))
    total = sum(sales(i) for i in range(100))
    facts = base_facts() + [{"key": "sales_total", "value": total, "unit": "USD", "source": "data/regions.csv",
                             "obtained": "data"}]
    for i, name in enumerate(REGIONS):
        share = (Decimal(sales(i)) / Decimal(total) * 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
        facts.append({"key": f"sales_{i}", "label": f"{name} sales", "value": sales(i), "unit": "USD",
                      "period": "2025", "source": "data/regions.csv", "obtained": "data"})
        facts.append({"key": f"share_{i}", "label": f"{name} share", "value": float(share), "unit": "%",
                      "obtained": "computed", "from": [f"sales_{i}", "sales_total"],
                      "formula": f"sales_{i} / sales_total * 100"})
    ledger(root, facts)
    return facts


def large_deck(path: Path, facts: list[dict], real: bool) -> None:
    """104 slides: an opening, the revenue line, 100 region slides, a closing and sources. Planted:
    three wrong figures (slides 17, 41, 77) and two leftovers (slide 52's prompt text, slide 90's
    empty title)."""
    shares = {f["key"]: f["value"] for f in facts}

    def empty_title(n):
        return (f'<p:sp><p:nvSpPr><p:cNvPr id="{n}" name="Title 1"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/>'
                '</p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>')

    slides = [[_shape(2, "Title 1", [["Regional review"]], "ctrTitle")],
              [_shape(2, "Title 1", [["Revenue held up"]], "title"), _shape(3, "Body", [["Revenue 2025: $4.2m"]])]]
    for i, name in enumerate(REGIONS):
        number = len(slides) + 1
        value, share = sales(i), shares[f"share_{i}"]
        body = [[f"Sales were ${value:,} in 2025, {share:.2f}% of the total"]]
        if number == 17:
            body = [[f"Sales were ${value + 1000:,} in 2025, {share:.2f}% of the total"]]      # wrong figure 1
        if number == 41:
            body.append(["Revenue 2025: $4.3m"])                                                 # wrong figure 2
        if number == 77:
            body = [[f"Sales were ${value:,} in 2025, 13.9% of the total"]]                      # wrong figure 3
        if number == 52:
            body.append(["Click to add text"])                                                   # leftover 1
        title = empty_title(2) if number == 90 else _shape(2, "Title 1", [[f"{name}: steady sales"]], "title")
        slides.append([title, _shape(3, "Body", body),
                       _table(4, [["Region", "Sales", "Share"], [name, f"{value / 1000:.1f}k", f"{share:.1f}%"]])])
    slides.append([_shape(2, "Title 1", [["What we ask for"]], "title"),
                   _shape(3, "Body", [["Hold the margin at 12.4% and aim for 18%"]])])
    slides.append([_shape(2, "Title 1", [["Sources"]], "title"), _shape(3, "Body", [["Annual report (s1)"]])])
    assert len(slides) == 104
    (make_real_pptx if real else make_pptx)(path, slides)


PLANTED_DECK = [(f"${sales(17 - 3) + 1000:,}", "Slide 17, Body"), ("$4.3m", "Slide 41, Body"), ("13.9%", "Slide 77, Body")]


def test_a_104_slide_deck_with_a_ledger_finds_exactly_what_was_planted(tmp_path):
    facts = large_project(tmp_path)
    large_deck(tmp_path / "deck.pptx", facts, real=False)
    started = time.perf_counter()
    result = check_file(tmp_path, "deck.pptx")
    took = time.perf_counter() - started
    print(f"\ncheck_file, 104 slides, {result['summary']['figures']} figures, ledger of {len(facts)} facts: {took:.2f} s")
    planted = [(i["text"], i["where"]) for i in result["items"] if i["status"] == "not_found"]
    assert planted == PLANTED_DECK
    assert [(p["text"], p["where"]) for p in result["placeholders"]] == [
        ("Click to add text", "Slide 52, Body"), ("(empty title)", "Slide 90, title")]
    assert result["conflicts"] == [{"label": "Revenue 2025", "figures": [
        {"text": "$4.2m", "where": "Slide 2, Body"}, {"text": "$4.3m", "where": "Slide 41, Body"}]}]
    summary = result["summary"]
    assert summary["figures"] == 404 and summary["not_found"] == 3 and summary["placeholders"] == 2
    assert summary["ledger"] == 401 and summary["conflicts"] == 1
    assert result["ledger"]["vouching"] == 207 and result["ledger"]["problems"] == []
    assert [p["part"] for p in result["by_part"]] == ["Slide 2", "Slide 17", "Slide 41", "Slide 52", "Slide 77",
                                                      "Slide 90"]
    share = next(i for i in result["items"] if i["where"] == "Slide 3, Body" and i["text"].endswith("%"))
    assert share["via"] == "ledger" and share["ref"] == "share_0" and share["note"].startswith("computed in the ledger")
    answer = filecheck.compact(result)
    assert len(json.dumps(answer, ensure_ascii=False)) < tools.RESULT_CAP and answer["lists_cut"] is False
    assert took < 20


def large_workbook(path: Path, facts: list[dict]) -> None:
    """50 sheets: Assumptions (the ledger's region sales, typed in) and 49 region sheets of 30 rows,
    each with typed figures, formulas and notes. Planted: three wrong typed figures (sheets 7, 23
    and 45) and two leftovers (TBD on sheet 12, a bracketed fill-in on sheet 30)."""
    book = openpyxl.Workbook()
    assumptions = book.active
    assumptions.title = "Assumptions"
    assumptions.append(["Key", "Label", "Value", "Unit"])
    for fact in facts:
        if fact["key"].startswith("sales_") and fact["key"] != "sales_total":
            assumptions.append([fact["key"], fact["label"], fact["value"], fact["unit"]])
    for n in range(1, 50):
        sheet = book.create_sheet(f"Region {n}")
        sheet.append(["Region", REGIONS[n], None, None, None, None, None, None])
        sheet.append(["Sales", sales(n) + (500 if n in (7, 23, 45) else 0), None, None, None, None, None, None])
        sheet.append(["Next year", f"=B2*1.05", None, None, None, None, None, None])
        sheet.append(["Note", "TBD" if n == 12 else "[insert source]" if n == 30 else "From the regional ledger"])
        for r in range(5, 31):
            sheet.append(["Detail", f"=B2/{r}", f"=C{r - 1}+1" if r > 5 else 1, "units", "ok", "ok", "ok", "ok"])
    book.save(path)


def test_a_50_sheet_workbook_with_a_ledger_finds_exactly_what_was_planted(tmp_path):
    facts = large_project(tmp_path)
    large_workbook(tmp_path / "book.xlsx", facts)
    started = time.perf_counter()
    result = check_file(tmp_path, "book.xlsx")
    took = time.perf_counter() - started
    print(f"\ncheck_file, 50 sheets, {result['summary']['figures']} items: {took:.2f} s")
    planted = [(i["text"], i["where"]) for i in result["items"] if i["status"] == "not_found"]
    assert planted == [(f"{sales(n) + 500}", f"'Region {n}'!B2 (Sales, {REGIONS[n]})") for n in (7, 23, 45)]
    assert [(p["text"], p["where"]) for p in result["placeholders"]] == [
        ("TBD", "'Region 12'!B4 (Note, Area BC)"), ("[insert source]", "'Region 30'!B4 (Note, Area DA)")]
    assert result["conflicts"] == [] and result["summary"]["placeholders"] == 2
    assert [p["part"] for p in result["by_part"]] == ["Region 7", "Region 12", "Region 23", "Region 30", "Region 45"]
    assert result["by_part"][1] == {"part": "Region 12", "not_found": 0, "placeholders": 1, "conflicts": 0}
    assumption = next(i for i in result["items"] if i["where"].startswith("Assumptions!C2"))
    assert assumption["via"] == "ledger" and assumption["ref"] == "sales_0"
    assert len(json.dumps(filecheck.compact(result), ensure_ascii=False)) < tools.RESULT_CAP
    assert took < 30


@needs_office
def test_a_104_slide_deck_is_drawn_in_ranges_from_one_pdf_with_an_overview(tmp_path):
    facts = large_project(tmp_path)
    large_deck(tmp_path / "deck.pptx", facts, real=True)
    assert [(i["text"], i["where"]) for i in check_file(tmp_path, "deck.pptx", save=False)["items"]
            if i["status"] == "not_found"] == PLANTED_DECK                 # the same deck, LibreOffice-ready
    started = time.perf_counter()
    first = office.render(tmp_path, "deck.pptx")
    took_first = time.perf_counter() - started
    assert first["page_count"] == 104 and len(first["pages"]) == 60 and first["pages_drawn"] == "1-60"
    assert first["reused_pdf"] is False and first["not_drawn"].startswith("Pages 61-104 of 104 have no image yet")
    assert first["not_drawn"].endswith('pages: "61-104".') and first["next"].startswith(first["not_drawn"])
    assert [Path(p).name for p in first["overview"]] == [f"overview-{n}.png" for n in range(1, 7)]
    started = time.perf_counter()
    rest = office.render(tmp_path, "deck.pptx", pages="61-104")
    took_rest = time.perf_counter() - started
    print(f"\nrender_document, 104 slides: first call {took_first:.1f} s (LibreOffice, 60 pages, overview); "
          f"pages 61-104 from the same PDF {took_rest:.1f} s")
    assert rest["reused_pdf"] is True and rest["pages_drawn"] == "61-104" and rest["not_drawn"] is None
    assert rest["pages"][0] == "deck.pptx.preview/page-61.png" and len(rest["pages"]) == 44
    viewer = (tmp_path / rest["preview"]).read_text()
    data = json.loads(viewer.split("const DATA = ", 1)[1].split(";\n", 1)[0]) if "const DATA = " in viewer else None
    assert data is None or data["numbers"] == list(range(1, 105))
    assert '"page-104.png"' in viewer
    # A changed file is converted again, and the images of the old one are gone.
    large_deck(tmp_path / "deck.pptx", facts[:-1] + [{**facts[-1]}], real=True)
    (tmp_path / "deck.pptx").write_bytes((tmp_path / "deck.pptx").read_bytes())
    with open(tmp_path / "deck.pptx", "ab") as handle:
        handle.write(b"\0")                                                 # not the same bytes any more
    again = office.render(tmp_path, "deck.pptx", pages="100-")
    assert again["reused_pdf"] is False and again["pages_drawn"] == "100-104"
    assert not (tmp_path / "deck.pptx.preview" / "page-1.png").exists()
    assert again["not_drawn"].startswith("Pages 1-99 of 104")


@needs_office
async def test_the_tools_answers_on_large_files_fit(studio, project):
    workspace = studio.store.workspace(project["id"])
    facts = large_project(workspace)
    large_workbook(workspace / "book.xlsx", facts)
    started = time.perf_counter()
    answer = await tools.call(studio, project["id"], "render_document", {"path": "book.xlsx"})
    took = time.perf_counter() - started
    print(f"\nrender_document, 50 sheets: {took:.1f} s")
    assert not answer.get("is_error"), answer
    text = answer["content"][0]["text"]
    assert len(text) < tools.RESULT_CAP and "(cut:" not in text
    result = json.loads(text)
    assert len(result["sheets"]) == 50 and result["error_total"] == 0
    assert any(s["start_cut"] for s in result["sheets"]) and "start is given for the first" in result["next"]
    deck = workspace / "deck.pptx"
    large_deck(deck, facts, real=True)
    answer = await tools.call(studio, project["id"], "render_document", {"path": "deck.pptx", "pages": "41-60"})
    result = json.loads(answer["content"][0]["text"])
    assert result["pages_drawn"] == "41-60" and result["not_drawn"].startswith("Pages 1-40, 61-104 of 104")
    bad = await tools.call(studio, project["id"], "render_document", {"path": "deck.pptx", "pages": "200"})
    assert bad.get("is_error") and "has 104 pages" in bad["content"][0]["text"]
    checked = await tools.call(studio, project["id"], "check_file", {"path": "deck.pptx"})
    assert len(checked["content"][0]["text"]) < tools.RESULT_CAP
    assert [i["where"] for i in json.loads(checked["content"][0]["text"])["not_found"]] == [w for _t, w in PLANTED_DECK]


# -- the studio's re-check -------------------------------------------------------------------------
async def test_the_recheck_counts_placeholders_and_says_what_it_did_not_read(client, studio, project, monkeypatch):
    from conftest import run_turn
    pid = project["id"]
    workspace = studio.store.workspace(pid)
    vouching_project(workspace)
    make_pptx(workspace / "deck.pptx", [[_shape(2, "Title 1", [["Click to add title"]], "title"),
                                         _shape(3, "Body", [["Revenue $4.2 million"]])]])
    make_docx(workspace / "memo.docx", _wp("Costs were $4,218,950."))
    (workspace / "index.html").write_text('<a href="deck.pptx">Deck</a><a href="memo.docx">Memo</a>')
    monkeypatch.setattr(studio.turns, "RECHECK_FILES", 1)
    events = await run_turn(client, studio, pid, "check file deck.pptx", options={"verify": True})
    recheck = next(e for e in events if e["type"] == "verification_recheck")
    assert recheck["problems"] == [{"page": "deck.pptx", "kind": "file", "what": "Click to add title",
                                    "where": "Slide 1, title", "why": filecheck.PLACEHOLDER_WHY}]
    assert recheck["not_rechecked"] == ["memo.docx"] and recheck["agrees"] is False
    (workspace / "deck.pptx").unlink()
    events = await run_turn(client, studio, pid, "check file memo.docx", options={"verify": True})
    recheck = next(e for e in events if e["type"] == "verification_recheck")
    assert recheck["problems"] == [] and recheck["not_rechecked"] == [] and recheck["agrees"] is True


# -- the skill and its helpers -----------------------------------------------------------------------
def test_the_skill_is_short_and_points_at_files_that_exist():
    import re
    skill = (LIBRARY / "skills" / "large-documents" / "SKILL.md").read_text()
    head = skill.split("---")[1]
    assert "name: large-documents" in head and len(re.search(r"description: (.*)", head).group(1)) <= 600
    assert len(skill.splitlines()) <= 160
    folder = LIBRARY / "skills" / "large-documents"
    for name in re.findall(r"`(reference/[\w./-]+)`", skill):
        assert (folder / name).is_file(), name
    for agent in ("section-writer", "slide-reviewer"):
        text = (LIBRARY / "agents" / f"{agent}.md").read_text()
        assert f"name: {agent}" in text and agent in skill
    assert "data/facts.json" in skill and "check_file" in skill and "render_document" in skill
    assert "renders in LibreOffice" in skill and "opens in PowerPoint" in skill
    assert len(skill.splitlines()) <= 150
    # The method is stated once; what is only for Office files or only for pages is in its own file.
    assert "`reference/office.md`" in skill and "`reference/html.md`" in skill
    office, pages = (folder / "reference" / "office.md").read_text(), (folder / "reference" / "html.md").read_text()
    assert "check_file" in office and "slide-reviewer" in office and "check_site" not in office
    assert "check_site" in pages and "check_page" in pages and "join-pages.py" in pages and "check_file" not in pages
    for name in ("deck-helper.js", "assemble-deck.js", "section-example.js", "word-and-excel.md", "review.md",
                 "plan-files.md", "join-pages.py"):
        assert f"`{name}`" in office + pages and (folder / "reference" / name).is_file(), name
    writer = (LIBRARY / "agents" / "section-writer.md").read_text()
    assert "html.md" in writer and "{{fact:<key>}}" in writer and "join_pages.py" in writer
