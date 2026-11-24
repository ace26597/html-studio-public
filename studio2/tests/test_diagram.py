"""Diagrams that are checked before they are shown (CONTRACT.md, "Diagrams and conversions": `render_diagram`).

Real drawings in the studio's own Chromium with the vendored Mermaid; no model calls. Skipped with
a reason when this machine has no cached Chromium.
"""

import hashlib
import json
import re
import xml.etree.ElementTree as ET

import pytest

from studio2_server import diagram, tools
from studio2_server.look import Looker

_looker = Looker()
needs_browser = pytest.mark.skipif(diagram.reason(_looker) is not None,
                                   reason=f"cannot draw diagrams here: {diagram.reason(_looker)}")

# One of each type the `diagrams` skill names, written against Mermaid 11.15.0.
GOOD = {
    "flowchart": ("flowchart LR\n  start[Order placed] --> pay{Paid?}\n  pay -->|yes| ship[Ship it]\n"
                  "  pay -->|no| remind[Send a reminder]\n  remind --> pay", ["Order placed", "Ship it"]),
    "sequence": ("sequenceDiagram\n  participant P as Person\n  participant S as Studio\n  P->>S: Ask for a page\n"
                 "  S-->>P: The page\n  Note over P,S: one request", ["Ask for a page", "The page"]),
    "state": ('stateDiagram-v2\n  state "Waiting for payment" as W\n  [*] --> W\n  W --> Paid: payment arrives\n'
              "  Paid --> [*]", ["Waiting for payment", "payment arrives"]),
    "er": ('erDiagram\n  CUSTOMER ||--o{ ORDER : "places"\n  ORDER ||--|{ LINE_ITEM : "contains"\n'
           "  CUSTOMER {\n    string name\n    int id PK\n  }", ["CUSTOMER", "LINE_ITEM", "places"]),
    "class": ("classDiagram\n  class Animal {\n    +String name\n    +eat()\n  }\n  Animal <|-- Dog", ["Animal", "Dog"]),
    "timeline": ("timeline\n  title Company history\n  2020 : Founded\n  2022 : First product\n  2024 : Second product",
                 ["Founded", "Second product"]),
    "gantt": ("gantt\n  title Plan\n  dateFormat YYYY-MM-DD\n  section Build\n  Design :a1, 2026-01-01, 10d\n"
              "  Code :after a1, 20d", ["Design", "Code"]),
}


@pytest.fixture
async def looker():
    seen = Looker()
    yield seen
    await seen.close()


def outside_references(svg: str) -> list[str]:
    root = ET.fromstring(svg)
    found = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1].lower() in ("script", "foreignobject", "image", "img", "iframe"):
            found.append(el.tag)
        for key, value in el.attrib.items():
            name = key.rsplit("}", 1)[-1].lower()
            if name.startswith("on") or (name in ("href", "src") and not value.startswith("#")):
                found.append(f"{name}={value}")
    found += re.findall(r"@import|url\(\s*['\"]?(?!#)[^)]*\)|https?://(?!www\.w3\.org/)", svg)
    return found


@needs_browser
@pytest.mark.parametrize("kind", list(GOOD))
async def test_each_type_renders_saves_and_holds_its_labels(kind, looker, tmp_path):
    source, labels = GOOD[kind]
    result = await diagram.render(looker, tmp_path, name=f"a-{kind}", source=source, must_show=labels)
    assert result["rendered"] and result["saved"] and result["missing"] == [], result
    folder = tmp_path / "files" / "diagrams"
    assert (folder / f"a-{kind}.mmd").read_text() == source + "\n"
    for key in ("svg", "svg_dark"):
        text = (tmp_path / result[key]).read_text()
        assert outside_references(text) == [], (key, outside_references(text))
        assert "<script" not in text.lower() and "<text" in text      # labels are text, not pictures of text
    assert result["safe"]["text_as_text"] and result["safe"]["scripts"] == 0
    assert result["size"]["width"] > 50 and result["size"]["height"] > 50
    assert result["nodes"], result
    assert "mermaid" not in result["embed"].lower().replace("diagrams/", "")   # the page gets the SVG, not the script
    assert result["blocked_requests"] == []


@needs_browser
async def test_light_and_dark_are_both_saved_and_differ(looker, tmp_path):
    result = await diagram.render(looker, tmp_path, name="both", source=GOOD["flowchart"][0])
    light, dark = (tmp_path / result["svg"]).read_text(), (tmp_path / result["svg_dark"]).read_text()
    assert light != dark and 'id="diagram-both"' in light and 'id="diagram-both-dark"' in dark   # both can sit in one page
    assert 'media="(prefers-color-scheme: dark)"' in result["embed"] and result["svg_dark"] in result["embed"]


@needs_browser
async def test_the_same_source_gives_the_same_bytes(looker, tmp_path):
    source = GOOD["er"][0]          # the entity boxes are drawn by code that calls Math.random
    digests = []
    for _ in range(2):
        result = await diagram.render(looker, tmp_path, name="same", source=source,
                                      png_width=600)
        digests.append([hashlib.sha256((tmp_path / result[k]).read_bytes()).hexdigest() for k in ("svg", "svg_dark", "png")])
    assert digests[0] == digests[1]


@needs_browser
async def test_a_gantt_chart_has_no_today_line_unless_asked(looker, tmp_path):
    result = await diagram.render(looker, tmp_path, name="plan", source=GOOD["gantt"][0])
    assert 'class="today"' not in (tmp_path / result["svg"]).read_text()
    assert (tmp_path / result["source"]).read_text() == GOOD["gantt"][0] + "\n"     # the saved source is as given


@needs_browser
async def test_a_broken_diagram_gives_the_line_and_saves_nothing(looker, tmp_path):
    good = await diagram.render(looker, tmp_path, name="flow", source=GOOD["flowchart"][0])
    before = {p.name: p.read_bytes() for p in (tmp_path / "files" / "diagrams").iterdir()}
    broken = "flowchart LR\n  A[Start] --> B\n  B --> C[Total (est.)]\n  C --> D"
    result = await diagram.render(looker, tmp_path, name="flow", source=broken)
    assert result["rendered"] is False and result["saved"] is False and good["rendered"]
    assert result["line"] == 3 and result["line_text"] == "  B --> C[Total (est.)]"
    assert "Parse error" in result["error"] and "three" in result["next"] and "table" in result["next"]
    after = {p.name: p.read_bytes() for p in (tmp_path / "files" / "diagrams").iterdir()}
    assert after == before                                           # the earlier good one is untouched
    typo = await diagram.render(looker, tmp_path, name="typo", source="%% a note\nflowchar LR\n  A --> B")
    assert typo["rendered"] is False and typo["line"] == 2 and not (tmp_path / "files/diagrams/typo.svg").exists()


@needs_browser
async def test_must_show_catches_a_missing_or_split_label(looker, tmp_path):
    # Without quotes the long state name becomes three states; the drawing still parses.
    source = "stateDiagram-v2\n  [*] --> Waiting for payment\n  payment --> Paid"
    result = await diagram.render(looker, tmp_path, name="states", source=source,
                                  must_show=["Waiting for payment", "Paid", "Refunded"])
    assert result["rendered"] and result["missing"] == ["Waiting for payment", "Refunded"]
    assert "not what was asked" in result["next"]
    plain = await diagram.render(looker, tmp_path, name="plain", source=GOOD["flowchart"][0])
    assert plain["must_show"] == 0 and plain["missing"] == [] and plain["nodes"] == 4 and plain["edges"] == 4
    assert {"Order placed", "Paid?", "yes", "no"} <= set(plain["labels"]) and "Compare" in plain["next"]


@needs_browser
async def test_a_wide_diagram_is_reported_hard_to_read(looker, tmp_path):
    chain = "flowchart LR\n" + "\n".join(f"  N{i}[Step number {i}] --> N{i + 1}[Step number {i + 1}]" for i in range(16))
    result = await diagram.render(looker, tmp_path, name="wide", source=chain)
    kinds = {f["kind"] for f in result["layout"]["findings"]}
    assert result["layout"]["hard_to_read"] and "too_wide" in kinds and result["size"]["width"] > 1400
    assert "split it" in result["next"] and "direction" in result["next"]
    tall = await diagram.render(looker, tmp_path, name="tall", source=chain.replace("flowchart LR", "flowchart TB"))
    assert not tall["layout"]["hard_to_read"] and tall["layout"]["smallest_text_at_desktop_px"] >= 9


@needs_browser
async def test_a_diagram_too_small_on_a_phone_is_said_without_being_a_finding(looker, tmp_path):
    # Five boxes in a row: fine on a desktop, a third of its size on a phone.
    row = "flowchart LR\n" + "\n".join(f"  N{i}[Step number {i}] --> N{i + 1}[Step number {i + 1}]" for i in range(5))
    wide = await diagram.render(looker, tmp_path, name="row", source=row)
    layout = wide["layout"]
    assert not layout["hard_to_read"] and layout["findings"] == []
    assert layout["small_on_phone"] and layout["smallest_text_at_phone_px"] < 9 < layout["smallest_text_at_desktop_px"]
    assert layout["smallest_text_at_phone_px"] == round(layout["smallest_text_px"] * diagram.PHONE / wide["size"]["width"], 1)
    assert "On a phone" in wide["next"] and "top to bottom" in wide["next"] and "open it large" in wide["next"]
    # Drawn top to bottom it is narrow enough: nothing is said.
    tall = await diagram.render(looker, tmp_path, name="column", source=row.replace("flowchart LR", "flowchart TB"))
    assert not tall["layout"]["small_on_phone"] and "On a phone" not in tall["next"]
    assert tall["layout"]["smallest_text_at_phone_px"] == tall["layout"]["smallest_text_px"]
    # A sequence diagram cannot be turned: it is told the size, not to turn it.
    talk = "sequenceDiagram\n" + "\n".join(f"  participant P{i} as Participant {i}" for i in range(6)) + "\n  P0->>P5: Hello"
    seq = await diagram.render(looker, tmp_path, name="talk", source=talk)
    assert seq["layout"]["small_on_phone"] and "On a phone" in seq["next"] and "top to bottom" not in seq["next"]


def test_which_sources_are_drawn_sideways():
    assert diagram.SIDEWAYS.search("flowchart LR\n  a --> b")
    assert diagram.SIDEWAYS.search("%% a note\ngraph RL\n  a --> b")
    assert diagram.SIDEWAYS.search("stateDiagram-v2\n  direction LR\n  [*] --> A")
    assert not diagram.SIDEWAYS.search("flowchart TB\n  a --> b")
    assert not diagram.SIDEWAYS.search("sequenceDiagram\n  A->>B: flowchart LR is a label")


@needs_browser
async def test_png_for_office_files_at_the_width_asked(looker, tmp_path):
    from PIL import Image
    result = await diagram.render(looker, tmp_path, name="deck", source=GOOD["sequence"][0], png_width=900)
    with Image.open(tmp_path / result["png"]) as image:
        assert image.width == 900 and image.height > 100 and image.getpixel((2, 2))[:3] == (255, 255, 255)
    with pytest.raises(diagram.DiagramError, match="png_width"):
        await diagram.render(looker, tmp_path, name="deck", source=GOOD["sequence"][0], png_width=50)


@needs_browser
async def test_links_and_outside_pictures_are_taken_out(looker, tmp_path):
    source = ('%%{init: {"securityLevel": "loose"}}%%\nflowchart LR\n  A[Home] --> B[Docs]\n'
              '  click B "https://example.com/docs" "Open"\n  style A fill:#fff,stroke:#333')
    result = await diagram.render(looker, tmp_path, name="linked", source=source, must_show=["Home", "Docs"])
    text = (tmp_path / result["svg"]).read_text()
    assert result["rendered"] and outside_references(text) == [] and "example.com" not in text
    assert result["safe"]["removed"] == ["a link to https://example.com/docs"]
    html = ('%%{init: {"htmlLabels": true, "flowchart": {"htmlLabels": true}}}%%\nflowchart LR\n'
            '  A["<b>Bold</b> <img src=https://example.com/a.png>"] --> B[Plain]')
    asked = await diagram.render(looker, tmp_path, name="html", source=html, must_show=["Bold", "Plain"])
    text = (tmp_path / asked["svg"]).read_text()
    assert asked["missing"] == [] and asked["safe"]["text_as_text"] is False and "foreignObject" in asked["next"]
    assert "a <img> element" in asked["safe"]["removed"] and "example.com" not in text
    pictured = await diagram.render(looker, tmp_path, name="pic", source=(
        'flowchart LR\n  A@{ img: "https://example.com/x.png", label: "Pic", pos: "t", w: 60, h: 60 }'))
    assert pictured["rendered"] is False and "fetches nothing" in pictured["error"]


@needs_browser
async def test_colours_follow_the_page_and_bad_values_are_refused(looker, tmp_path):
    result = await diagram.render(looker, tmp_path, name="brand", source=GOOD["flowchart"][0],
                                  colors={"light": {"primaryColor": "#ffe8cc", "lineColor": "#8a4b08"},
                                          "dark": {"primaryColor": "#3b2a14", "primaryTextColor": "#f5e6d3"}})
    assert "#ffe8cc" in (tmp_path / result["svg"]).read_text().lower()
    assert "#3b2a14" in (tmp_path / result["svg_dark"]).read_text().lower()
    with pytest.raises(diagram.DiagramError, match="colour"):
        await diagram.render(looker, tmp_path, name="brand", source=GOOD["flowchart"][0],
                             colors={"light": {"primaryColor": "red;}</style><script>x()</script>"}})


@needs_browser
async def test_a_mmd_file_of_the_project_is_read_and_paths_stay_inside(looker, tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "flow.mmd").write_text(GOOD["flowchart"][0])
    result = await diagram.render(looker, tmp_path, name="from-file", path="notes/flow.mmd")
    assert result["rendered"] and (tmp_path / "files/diagrams/from-file.mmd").read_text().startswith("flowchart LR")
    (tmp_path.parent / "outside.mmd").write_text(GOOD["flowchart"][0])
    for bad in ("../outside.mmd", "notes/../../outside.mmd", ".git/x.mmd"):
        with pytest.raises(diagram.DiagramError, match="inside the project"):
            await diagram.render(looker, tmp_path, name="x", path=bad)
    for bad in ("/etc/hosts", "notes/flow.txt"):          # read as project paths, which do not exist
        with pytest.raises(diagram.DiagramError, match="no .mmd file"):
            await diagram.render(looker, tmp_path, name="x", path=bad)
    for name in ("../up", "a/b", "Big Name", ""):
        with pytest.raises(diagram.DiagramError, match="name"):
            await diagram.render(looker, tmp_path, name=name, source=GOOD["flowchart"][0])


def test_the_vendored_mermaid_is_the_pinned_version():
    note = (diagram.VENDOR.parent / "THIRD_PARTY.md").read_text()
    assert diagram.MERMAID_VERSION in note and "MIT" in note
    assert (diagram.VENDOR.parent / "LICENSE").read_text().startswith("The MIT License")
    digest = hashlib.sha256(diagram.VENDOR.read_bytes()).hexdigest()
    assert digest in note


def test_a_gantt_source_gets_no_today_line_but_keeps_its_own_choice():
    assert diagram.no_today_line("gantt\n  title X") == "gantt\n    todayMarker off\n  title X"
    assert diagram.no_today_line("gantt\n  todayMarker on\n") == "gantt\n  todayMarker on\n"
    assert diagram.no_today_line("flowchart LR\n A-->B") == "flowchart LR\n A-->B"


class _Studio:
    def __init__(self, looker, workspace):
        self.looker = looker
        self.store = type("S", (), {"workspace": lambda _self, _pid: workspace})()


@needs_browser
async def test_the_tool_answers_through_the_studio(looker, tmp_path):
    studio = _Studio(looker, tmp_path)
    answer = await tools.call(studio, "p1", "render_diagram", {"name": "t", "source": GOOD["class"][0],
                                                               "must_show": ["Animal"]})
    body = json.loads(answer["content"][0]["text"])
    assert not answer.get("is_error") and body["svg"] == "files/diagrams/t.svg" and body["missing"] == []
    refused = await tools.call(studio, "p1", "render_diagram", {"name": "t", "path": "../x.mmd"})
    assert refused["is_error"] and "inside the project" in refused["content"][0]["text"]


async def test_the_agent_is_told_and_the_call_is_named(studio, project):
    from studio2_server.harness.claude import _studio_tool
    assert _studio_tool("render_diagram", {"name": "order-flow"}.get) == ("execute", "Draw the diagram “order-flow”")
    assert _studio_tool("convert_document", {"path": "files/r.md", "to": ".docx"}.get) == (
        "execute", "Convert files/r.md to DOCX")
    told = (studio.settings.library_dir / "system-prompt.md").read_text()      # said once, in the system prompt
    assert "render_diagram" in told and "convert_document" in told and "`diagrams` skill" in told
    listed = {tool["name"]: tool for tool in tools.describe(studio)}
    assert listed["render_diagram"]["available"] is (diagram.reason(studio.looker) is None)


@needs_browser
async def test_every_example_in_the_skill_draws(looker, tmp_path):
    from pathlib import Path
    skill = (Path(diagram.__file__).parents[2] / "library" / "skills" / "diagrams" / "SKILL.md").read_text()
    blocks = re.findall(r"```mermaid\n(.*?)```", skill, re.S)
    assert len(blocks) == 7
    for number, block in enumerate(blocks):
        result = await diagram.render(looker, tmp_path, name=f"skill-{number}", source=block)
        assert result["rendered"] and not result["layout"]["findings"], (block, result.get("error"), result.get("layout"))
