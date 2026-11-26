"""The contract split by area (studio2/tools/contract_split.py): the split moves sections word for
word, the index lists every heading, and the check finds every quoted section name. Before the split
the same check runs on the single file. No model calls, no studio."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "studio2" / "tools" / "contract_split.py"

spec = importlib.util.spec_from_file_location("contract_split", TOOL)
cs = importlib.util.module_from_spec(spec)
sys.modules.setdefault("contract_split", cs)
spec.loader.exec_module(cs)

# Written in two parts so this file is not itself read as a reference to the contract.
CITE = "CONTRACT" + ".md"
APPS, CORE = "contract" + "/apps.md", "contract" + "/core.md"


def tree(tmp_path: Path, contract: str, refs: str = "") -> Path:
    (tmp_path / "studio2").mkdir()
    (tmp_path / "studio2" / "CONTRACT.md").write_text(contract, encoding="utf-8")
    if refs:
        (tmp_path / "notes.md").write_text(refs, encoding="utf-8")
    return tmp_path


SMALL = """# First part

Intro words.

## REST

Routes.

```sh
# not a heading, a shell comment
```

# Second part

**A named paragraph.** Text.

## Events

Events here.

# Third part

Last words, and no blank line at the end.
"""

SMALL_AREAS = {"First part": "core", "Second part": "apps", "Third part": "core"}


# ---- this repository ---------------------------------------------------------------------------


def test_this_repository_contract_and_its_references_check(capsys):
    assert cs.main(["--check"]) == 0, capsys.readouterr().out
    assert "contract check passed" in capsys.readouterr().out


def test_this_contract_splits_without_loss(tmp_path, capsys):
    """On a copy: the real contract splits with the real table (or, already split, its index is
    current), and a second run changes nothing."""
    (tmp_path / "studio2").mkdir()
    shutil.copy(cs.contract_file(REPO), tmp_path / "studio2" / "CONTRACT.md")
    if cs.is_split(REPO):
        shutil.copytree(cs.contract_dir(REPO), tmp_path / "studio2" / "contract")
        before = {p.name: p.read_bytes() for p in (tmp_path / "studio2").rglob("*.md")}
        assert cs.main(["--index", "--root", str(tmp_path)]) == 0, capsys.readouterr()
    else:
        original = cs.contract_file(REPO).read_text(encoding="utf-8")
        assert cs.main(["--root", str(tmp_path)]) == 0, capsys.readouterr()
        out = capsys.readouterr().out
        assert cs.sha(original) in out and "proof after writing" in out
        joined = "".join(s.text for _, s in cs.current_order(tmp_path))
        assert joined == original
        before = {p.name: p.read_bytes() for p in (tmp_path / "studio2").rglob("*.md")}
        assert cs.main(["--root", str(tmp_path)]) == 0
        assert "already an index" in capsys.readouterr().out
    after = {p.name: p.read_bytes() for p in (tmp_path / "studio2").rglob("*.md")}
    assert after == before


# ---- the split, on a small contract -------------------------------------------------------------


def test_split_moves_sections_word_for_word_and_indexes_every_heading(tmp_path, capsys):
    root = tree(tmp_path, SMALL, f'See {CITE}, "Events" and {CITE}, "A named paragraph".\n')
    assert cs.split(root, SMALL_AREAS) == 0
    core = (root / "studio2/contract/core.md").read_text()
    apps = (root / "studio2/contract/apps.md").read_text()
    assert core == cs.area_head("core") + cs.sections(SMALL)[0].text + cs.sections(SMALL)[2].text
    assert apps == cs.area_head("apps") + cs.sections(SMALL)[1].text
    index = (root / "studio2/CONTRACT.md").read_text()
    assert [line.strip() for line in index.split(cs.INDEX_MARKER)[1].strip().split("\n")] == [
        "- [First part](contract/core.md#first-part)",
        "- [REST](contract/core.md#rest)",
        "- [Second part](contract/apps.md#second-part)",
        "- [Events](contract/apps.md#events)",
        "- [Third part](contract/core.md#third-part)",
    ]
    assert "".join(s.text for _, s in cs.current_order(root)) == SMALL
    assert cs.check(root) == 0
    # run again: nothing changes
    files = {p: p.read_bytes() for p in (root / "studio2").rglob("*.md")}
    assert cs.split(root, SMALL_AREAS) == 0
    assert "already an index" in capsys.readouterr().out
    assert {p: p.read_bytes() for p in (root / "studio2").rglob("*.md")} == files


def test_an_unknown_heading_stops_the_split_and_says_where_to_add_it(tmp_path, capsys):
    root = tree(tmp_path, SMALL + "\n# Fourth part\n\nNew.\n")
    with pytest.raises(cs.SplitError) as error:
        cs.split(root, SMALL_AREAS)
    assert "Fourth part" in str(error.value) and "ADD NEW HEADINGS HERE" in str(error.value)
    assert not (root / "studio2/contract").exists()
    assert "ADD NEW HEADINGS HERE" in TOOL.read_text()
    assert cs.main(["--root", str(root)]) == 1


def test_index_lists_a_new_section_after_the_one_before_it(tmp_path):
    root = tree(tmp_path, SMALL)
    cs.split(root, SMALL_AREAS)
    apps = root / "studio2/contract/apps.md"
    apps.write_text(apps.read_text() + "\n# Added later\n\n## REST\n\nMore routes.\n")
    assert cs.check(root) == 1
    assert cs.rebuild_index(root) == 0
    titles = [s.title for _, s in cs.current_order(root)]
    assert titles == ["First part", "Second part", "Added later", "Third part"]
    index = (root / "studio2/CONTRACT.md").read_text()
    assert "(contract/apps.md#rest)" in index and "(contract/core.md#rest)" in index
    assert cs.check(root) == 0


def test_check_finds_a_quote_that_is_no_longer_a_heading(tmp_path, capsys):
    root = tree(tmp_path, SMALL, f'See {CITE}, "Events".\nAnd {CITE} section "Gone away".\n')
    assert cs.check(root) == 1  # the single file too
    assert "notes.md:2: 'Gone away'" in capsys.readouterr().out
    cs.split(root, SMALL_AREAS)
    assert cs.check(root) == 1
    (root / "notes.md").write_text(f'See {CITE}, "Events" and {APPS}, "Second".\n')
    assert cs.check(root) == 0
    (root / "notes.md").write_text(f'See {CORE}, "Second part".\n')
    assert cs.check(root) == 1  # the heading is in another area's file


def test_check_finds_a_heading_in_two_files_and_an_index_out_of_step(tmp_path, capsys):
    root = tree(tmp_path, SMALL)
    cs.split(root, SMALL_AREAS)
    apps = root / "studio2/contract/apps.md"
    apps.write_text(apps.read_text() + "\n# Third part\n\nTwice.\n")
    cs.rebuild_index(root)
    capsys.readouterr()
    assert cs.check(root) == 1
    assert "more than one place" in capsys.readouterr().out
    apps.write_text(apps.read_text().replace("\n# Third part\n\nTwice.\n", ""))
    cs.rebuild_index(root)
    assert cs.check(root) == 0
    index = root / "studio2/CONTRACT.md"
    index.write_text(index.read_text().replace("- [REST](contract/core.md#rest)\n", ""))
    assert cs.check(root) == 1
    assert "not in the index: 'REST'" in capsys.readouterr().out
