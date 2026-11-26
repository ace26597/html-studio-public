#!/usr/bin/env python3
"""Split the studio's contract into one file per area, keep an index, and check the references.

    python3 studio2/tools/contract_split.py            split a single-file CONTRACT.md (does nothing if split)
    python3 studio2/tools/contract_split.py --check    check the contract and every reference to it
    python3 studio2/tools/contract_split.py --index    rebuild the index in CONTRACT.md from contract/*.md

The contract was one file, `studio2/CONTRACT.md`. Split, each top-level section (a `# ` heading
and everything up to the next one) moves word for word into `studio2/contract/<area>.md`, keeping
its order inside the area, and `CONTRACT.md` becomes a short introduction and an index: every
heading, in the order the single file had them, as a link to its file and anchor.

The split is decided by AREA_OF below, one line per top-level heading. A heading the table does
not know stops the split with the list of such headings; add each one to AREA_OF, at the place
marked "ADD NEW HEADINGS HERE", and run the split again.

Before writing anything the split proves that nothing was lost: the sections joined in index order
are the original file, byte for byte. After writing it reads the files back and proves it again.

After the split, a new section goes in the area's file under `studio2/contract/`; then
`--index` rebuilds the index. A section already listed keeps its place in the index; a new one is
listed after the section that comes before it in its file.

`--check` fails when:
- a section name quoted next to the contract's file name anywhere in the repository is not a
  heading (or the bold or italic name of a paragraph) in the contract;
- a top-level heading is in two places;
- the index and the area files disagree (a heading missing, extra, moved or with the wrong link).
On a single-file contract only the first check applies.

Standard library only. Exit 0 when all is well, 1 with the reasons otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The area files, in the order the index lists them: file stem and title.
AREAS: dict[str, str] = {
    "core": "Core",
    "library": "Library",
    "access": "Access and safety",
    "apps": "Apps",
    "models": "Models, setup and cost",
    "output": "Output and checking",
    "interface": "Interface and the agent's words",
}

# Each top-level heading of the single-file contract, exactly as written after "# ", and its area.
# The "Step N additions" sections hold several subjects each; they move whole, to the area most of
# them belong to, and the index still finds every heading inside them.
AREA_OF: dict[str, str] = {
    "Studio 2 — API and event contract (Step 1)": "core",
    "Step 2 additions": "apps",
    "Step 2b additions: what the studio can do, and the person's own databases": "library",
    "Step 3 additions: the capability library": "library",
    "Access from other devices (phone, iPad, another computer)": "access",
    "Draw on the page": "interface",
    "Models for a project's back end": "apps",
    "Office documents": "output",
    "Voice input": "interface",
    "Templates the person saves": "core",
    "Video and images from a page": "output",
    "Generated media": "output",
    "Bring an app by its folder (step 3k)": "apps",
    "Ship: make it live (step 3l)": "apps",
    "Install and first run (step 3m)": "models",
    "Any model (step 3n)": "models",
    "Step 3o: a library that picks for itself": "library",
    "How much to do, and checking what is on the page (depth and verify)": "output",
    "Suggested next steps": "interface",
    "Examples that come with the studio": "core",
    "Export: take a page or an app out of the studio": "core",
    "Keys for other services": "models",
    "More places to look for skills and connectors": "library",
    "Skills a person makes": "library",
    "Verify content reaches files": "output",
    "Seeing an app signed in as the person": "access",
    "Files in the chat, and files shown as what they are": "interface",
    "Diagrams and conversions": "output",
    "Very large documents": "output",
    "Context from this computer: folders, files and addresses": "core",
    "The guide and the benchmarks, inside the studio": "interface",
    "Reading any skill, explaining it, and starting from one": "library",
    "A connector from a description": "library",
    "Instructions for a project": "interface",
    "How much to involve the person (the scale, and the agent's cards)": "interface",
    "The chat panel on a desktop: its size, and where the options are": "interface",
    "Which code a studio is running": "core",
    "The owner of a shared studio": "access",
    "A page with its own address signs in by itself": "access",
    "Files are dropped on the chat, nowhere else": "interface",
    "A picture in the page opens large": "interface",
    "Long pasted text becomes an attachment": "interface",
    "Show what changed, when asked": "interface",
    "Find on the page and in the chat": "interface",
    "What an agent's shell may not reach": "access",
    "What the agent is told once, and what with each message": "interface",
    "What a guest may not do on a shared studio": "access",
    "Projects of many pages": "output",
    "Big projects: a plan the person sees, notes that last, and room for a few requests at once": "core",
    "`query_files` on big files, and what its SQL may read": "output",
    "Tests for a project's app: `run_tests`": "output",
    "Where a shell command is judged from": "access",
    "Text a command only rewrites or prints": "access",
    "Files too large for versions": "core",
    "Find words in a project's pages and across projects": "core",
    # ---- ADD NEW HEADINGS HERE: "Exact heading text after '# '": "<area>", (areas: see AREAS) ----
}

INDEX_MARKER = "<!-- contract_split.py: the index below is rebuilt by `contract_split.py --index` -->"

INTRO = """# Studio 2: the contract

What the app must do: its API, events, tools and rules. Server and web UI are built against it.
Change the contract first, then both sides.

The contract is kept in one file per area under [`contract/`](contract/). This file is the index:
every heading, in the order the sections were written, with a link to where it is now. Some
sections say "above" or "earlier"; that means earlier in this order.

To add a section: put it in the area's file under `contract/`, then run
`python3 studio2/tools/contract_split.py --index` to rebuild the index below, and
`python3 studio2/tools/contract_split.py --check` to check it and every reference to it.

## Areas

{areas}

## Index

{marker}

{index}
"""

AREA_HEAD = """# Contract: {title}

One area of the studio's contract. The index of every section, in the order they were written, is
[CONTRACT.md](../CONTRACT.md).

"""

# The contract's file name as other files cite it; the quoted section name comes right after.
_NAME = "CONTRACT"
REFERENCE = re.compile(
    r"(?P<file>" + _NAME + r"(?:\.md)?|contract/(?P<area>[a-z-]+)\.md)`?(?:\]\([^)\s]*\))?"
    r"(?:[,:;]?[ \t]+(?:section[ \t]+)?|[,:;]|[ \t]*\([ \t]*)\"(?P<name>[^\"\n]{3,90})\""
)
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "data", "__pycache__", ".claude"}


class SplitError(Exception):
    """A reason the split, the index or the check cannot go on. The message says what to do."""


@dataclass
class Heading:
    level: int
    text: str
    anchor: str = ""


@dataclass
class Section:
    """A top-level section: its `# ` heading line and everything up to the next one."""
    title: str  # the heading text; "" for text before the first heading
    text: str


# ---------------------------------------------------------------------------------------------
# Reading markdown


def _lines(text: str):
    """Each line with its offset and whether it is inside fenced code (a fence line counts as inside)."""
    fence = ""
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if fence:
            if stripped.startswith(fence) and stripped.strip(fence[0]) == "":
                fence = ""
            yield offset, line, True
        else:
            match = re.match(r"(`{3,}|~{3,})", stripped)
            if match:
                fence = match.group(1)
            yield offset, line, bool(match)
        offset += len(line)


HEADING = re.compile(r"(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")


def headings(text: str) -> list[Heading]:
    """The ATX headings of a markdown text, outside fenced code, in order."""
    found: list[Heading] = []
    for _, line, fenced in _lines(text):
        match = None if fenced else HEADING.match(line.rstrip("\r\n"))
        if match:
            found.append(Heading(len(match.group(1)), match.group(2)))
    return found


def sections(text: str) -> list[Section]:
    """The text cut at each top-level heading outside fenced code. Joined, they are the text."""
    cuts: list[tuple[int, str]] = []
    for offset, line, fenced in _lines(text):
        match = None if fenced else HEADING.match(line.rstrip("\r\n"))
        if match and len(match.group(1)) == 1:
            cuts.append((offset, match.group(2)))
    result: list[Section] = []
    if not cuts or cuts[0][0] > 0:
        result.append(Section("", text[: cuts[0][0] if cuts else len(text)]))
    for i, (start, title) in enumerate(cuts):
        end = cuts[i + 1][0] if i + 1 < len(cuts) else len(text)
        result.append(Section(title, text[start:end]))
    return result


def slug(text: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces made hyphens."""
    text = text.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchored(found: list[Heading]) -> list[Heading]:
    """The headings with their anchors, numbered the way GitHub numbers repeats in one file."""
    seen: dict[str, int] = {}
    for heading in found:
        base = slug(heading.text)
        if base in seen:
            seen[base] += 1
            heading.anchor = f"{base}-{seen[base]}"
        else:
            seen[base] = 0
            heading.anchor = base
    return found


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("`", "")).strip()


# ---------------------------------------------------------------------------------------------
# Places


def contract_file(root: Path) -> Path:
    return root / "studio2" / "CONTRACT.md"


def contract_dir(root: Path) -> Path:
    return root / "studio2" / "contract"


def is_split(root: Path) -> bool:
    path = contract_file(root)
    return path.exists() and INDEX_MARKER in path.read_text(encoding="utf-8")


def area_head(area: str) -> str:
    return AREA_HEAD.format(title=AREAS[area])


def read_area(root: Path, area: str) -> list[Section]:
    """The sections of one area file, without the short head the split wrote above them."""
    path = contract_dir(root) / f"{area}.md"
    text = path.read_text(encoding="utf-8")
    head = area_head(area)
    if not text.startswith(head):
        first = text.split("\n", 1)[0]
        raise SplitError(f"{path.relative_to(root)}: does not start with the area's head "
                         f"({head.splitlines()[0]!r}); its first line is {first!r}. Put the head back.")
    found = sections(text[len(head):])
    if found and found[0].title == "":
        if found[0].text.strip():
            raise SplitError(f"{path.relative_to(root)}: text between the area's head and its first "
                             f"'# ' heading. Move it into a section.")
        found = found[1:]
    return found


def area_files(root: Path) -> list[str]:
    folder = contract_dir(root)
    if not folder.is_dir():
        return []
    stray = sorted(p.name for p in folder.iterdir() if p.suffix == ".md" and p.stem not in AREAS)
    if stray:
        raise SplitError(f"files in studio2/contract/ that are not areas: {', '.join(stray)}. "
                         f"Add them to AREAS in studio2/tools/contract_split.py or move their sections.")
    return [area for area in AREAS if (folder / f"{area}.md").exists()]


# ---------------------------------------------------------------------------------------------
# The index


def index_lines(order: list[tuple[str, Section]]) -> list[str]:
    """One line per heading, in the given order of sections, each a link to its area file."""
    per_file: dict[str, list[Heading]] = {}
    for area, section in order:
        per_file.setdefault(area, [])
    # anchors are counted per file, over the file's own order (head first, then sections)
    anchors: dict[tuple[str, int], list[Heading]] = {}
    for area in per_file:
        mine = [s for a, s in order if a == area]
        all_heads = anchored(headings(area_head(area) + "".join(s.text for s in mine)))[1:]
        position = 0
        for i, section in enumerate(mine):
            count = len(headings(section.text))
            anchors[(area, i)] = all_heads[position:position + count]
            position += count
    lines: list[str] = []
    seen: dict[str, int] = {}
    for area, section in order:
        i = seen.get(area, 0)
        seen[area] = i + 1
        for heading in anchors[(area, i)]:
            indent = "  " * (heading.level - 1)
            label = heading.text.replace("[", "\\[").replace("]", "\\]")
            lines.append(f"{indent}- [{label}](contract/{area}.md#{heading.anchor})")
    return lines


LINK = re.compile(r"^(?P<indent> *)- \[(?P<label>.*)\]\(contract/(?P<area>[a-z-]+)\.md#(?P<anchor>[^)]*)\)$")


def write_index(root: Path, order: list[tuple[str, Section]]) -> str:
    areas = []
    for area in AREAS:
        mine = [s for a, s in order if a == area]
        if mine:
            areas.append(f"- [{AREAS[area]}](contract/{area}.md): {len(mine)} sections")
    text = INTRO.format(areas="\n".join(areas), marker=INDEX_MARKER, index="\n".join(index_lines(order)))
    return text


def read_index(root: Path) -> list[tuple[str, str, int, str]]:
    """The index's lines as (area, anchor, level, label)."""
    text = contract_file(root).read_text(encoding="utf-8")
    body = text.split(INDEX_MARKER, 1)[1]
    found = []
    for line in body.split("\n"):
        if not line.strip():
            continue
        match = LINK.match(line)
        if not match:
            raise SplitError(f"studio2/CONTRACT.md: a line in the index is not a link to an area file: "
                             f"{line!r}. Rebuild it with --index.")
        label = match.group("label").replace("\\[", "[").replace("\\]", "]")
        found.append((match.group("area"), match.group("anchor"), len(match.group("indent")) // 2 + 1, label))
    return found


# ---------------------------------------------------------------------------------------------
# Split


def plan_split(text: str, area_of: dict[str, str]) -> list[tuple[str, Section]]:
    found = sections(text)
    if found and found[0].title == "":
        raise SplitError("CONTRACT.md has text before its first '# ' heading. Put it under a heading first.")
    unknown = [s.title or "(text before the first heading)" for s in found if s.title not in area_of]
    if unknown:
        raise SplitError(
            "top-level headings the split does not know:\n"
            + "\n".join(f"  - {title}" for title in unknown)
            + "\nAdd each one to AREA_OF in studio2/tools/contract_split.py, at the line marked "
              "'ADD NEW HEADINGS HERE' (exact text after '# '; areas: " + ", ".join(AREAS) + "), "
              "then run the split again.")
    bad = sorted({area_of[s.title] for s in found} - set(AREAS))
    if bad:
        raise SplitError(f"AREA_OF names areas that are not in AREAS: {', '.join(bad)}")
    titles = [s.title for s in found]
    twice = sorted({t for t in titles if titles.count(t) > 1})
    if twice:
        raise SplitError("top-level headings that appear twice in the single file: " + "; ".join(twice))
    return [(area_of[s.title], s) for s in found]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split(root: Path, area_of: dict[str, str] | None = None, out=print) -> int:
    area_of = AREA_OF if area_of is None else area_of
    path = contract_file(root)
    if is_split(root):
        out("studio2/CONTRACT.md is already an index: the contract is split. Nothing was changed.")
        out("To add a section, put it in studio2/contract/<area>.md and run this with --index.")
        return 0
    if area_files(root):
        raise SplitError("studio2/contract/ already has area files but CONTRACT.md is not an index. "
                         "Remove the area files or restore the index before splitting.")
    original = path.read_text(encoding="utf-8")
    order = plan_split(original, area_of)

    joined = "".join(s.text for _, s in order)
    if joined != original:
        raise SplitError("internal: the sections joined in order are not the original file. Nothing written.")
    out(f"proof before writing: {len(order)} top-level sections, joined in index order, are the original "
        f"file byte for byte ({len(original.encode('utf-8'))} bytes, sha256 {sha(original)}).")

    files = {area: area_head(area) + "".join(s.text for a, s in order if a == area) for area in AREAS}
    files = {area: body for area, body in files.items() if body != area_head(area)}
    index = write_index(root, order)

    folder = contract_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    for area, body in files.items():
        (folder / f"{area}.md").write_text(body, encoding="utf-8")
    path.write_text(index, encoding="utf-8")

    # read back: each area's sections are the original ones, in their original order
    back = {area: read_area(root, area) for area in files}
    rebuilt = []
    taken = {area: 0 for area in back}
    for area, section in order:
        got = back[area][taken[area]]
        taken[area] += 1
        if got.title != section.title or got.text != section.text:
            raise SplitError(f"read back: the section {section.title!r} in contract/{area}.md is not the "
                             f"original. The files are written; restore CONTRACT.md from git and look.")
        rebuilt.append(got.text)
    leftover = [area for area in back if taken[area] != len(back[area])]
    if leftover or "".join(rebuilt) != original:
        raise SplitError("read back: the area files do not hold exactly the original sections.")
    out(f"proof after writing: the sections read back from studio2/contract/, joined in index order, have "
        f"sha256 {sha(''.join(rebuilt))}, the same as the original.")
    for area, body in files.items():
        count = sum(1 for a, _ in order if a == area)
        out(f"  contract/{area}.md: {count} sections, {len(body.encode('utf-8'))} bytes")
    out(f"  CONTRACT.md (index): {len(index_lines(order))} headings, {len(index.encode('utf-8'))} bytes")
    return 0


# ---------------------------------------------------------------------------------------------
# Index rebuild


def current_order(root: Path) -> list[tuple[str, Section]]:
    """The sections in the order the index lists them; a section the index lacks goes after the one
    before it in its own file (or first, if it is the file's first)."""
    present = {area: read_area(root, area) for area in area_files(root)}
    # which (area, top-level anchor) the index lists, in its order
    listed: list[tuple[str, str]] = []
    if is_split(root):
        for area, anchor, level, _ in read_index(root):
            if level == 1:
                listed.append((area, anchor))
    keyed: dict[tuple[str, str], tuple[str, int]] = {}
    for area, found in present.items():
        heads = anchored(headings(area_head(area) + "".join(s.text for s in found)))[1:]
        top = [h for h in heads if h.level == 1]
        for i, heading in enumerate(top):
            keyed[(area, heading.anchor)] = (area, i)
    order: list[tuple[str, int]] = [keyed[k] for k in listed if k in keyed]
    placed = set(order)
    for area, found in present.items():
        for i in range(len(found)):
            if (area, i) in placed:
                continue
            before = [j for j in range(i) if (area, j) in placed]
            if before:
                at = order.index((area, max(before))) + 1
            else:
                after = [j for j in range(i + 1, len(found)) if (area, j) in placed]
                at = order.index((area, min(after))) if after else len(order)
            order.insert(at, (area, i))
            placed.add((area, i))
    # inside one file the file's own order wins: each area's places in the index take its
    # sections in the order the file has them
    slots: dict[str, list[int]] = {}
    for area, i in order:
        slots.setdefault(area, []).append(i)
    taken = {area: iter(sorted(found)) for area, found in slots.items()}
    order = [(area, next(taken[area])) for area, _ in order]
    return [(area, present[area][i]) for area, i in order]


def rebuild_index(root: Path, out=print) -> int:
    if not is_split(root):
        raise SplitError("the contract is still one file; there is no index to rebuild. Split it first.")
    before = contract_file(root).read_text(encoding="utf-8")
    order = current_order(root)
    text = write_index(root, order)
    if text == before:
        out("the index already matches studio2/contract/. Nothing was changed.")
    else:
        contract_file(root).write_text(text, encoding="utf-8")
        out(f"rebuilt the index in studio2/CONTRACT.md: {len(order)} top-level sections, "
            f"{len(index_lines(order))} headings.")
    return 0


# ---------------------------------------------------------------------------------------------
# Check


def repository_files(root: Path) -> list[Path]:
    try:
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
                                 "--exclude-standard"], capture_output=True, check=True).stdout
        names = sorted({n for n in listed.decode("utf-8", "replace").split("\0") if n})
        paths = [root / n for n in names]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in sorted(root.rglob("*"))
                 if not any(part in SKIP_DIRS for part in p.relative_to(root).parts)]
    return [p for p in paths if p.is_file() and not p.is_symlink()]


def references(root: Path) -> list[tuple[str, int, str | None, str]]:
    """Every quoted section name next to the contract's file name: (file, line, area or None, name)."""
    found = []
    skip = {contract_file(root).resolve()}
    folder = contract_dir(root).resolve()
    for path in repository_files(root):
        resolved = path.resolve()
        if resolved in skip or folder in resolved.parents:
            continue
        try:
            if path.stat().st_size > 4_000_000:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192] or _NAME.encode() not in raw and b"contract/" not in raw:
            continue
        text = raw.decode("utf-8", "replace")
        for match in REFERENCE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append((str(path.relative_to(root)), line, match.group("area"), match.group("name")))
    return found


def resolves(name: str, heads: list[str], body: str) -> bool:
    """A quoted name is good when a heading holds it (whole words, any case), or a paragraph starts with it."""
    name = re.sub(r"(?:…|\.\.\.)$", "", name.strip())  # a quote cut short with an ellipsis
    want = re.compile(r"(?<!\w)" + re.escape(plain(name)) + r"(?!\w)", re.IGNORECASE)
    for heading in heads:
        if want.search(plain(heading)):
            return True
    flat = body.replace("`", "")
    target = plain(name)
    pattern = r"(?:\*\*|(?<![*\w])\*|^[ \t]*(?:- )?)" + re.escape(target).replace(r"\ ", r"\s+")
    return re.search(pattern, flat, re.MULTILINE) is not None


def check(root: Path, out=print) -> int:
    problems: list[str] = []
    split_now = is_split(root)
    if split_now:
        areas = area_files(root)
        per_area = {area: read_area(root, area) for area in areas}
        bodies = {area: (contract_dir(root) / f"{area}.md").read_text(encoding="utf-8") for area in areas}
        heads = {area: [h.text for h in headings("".join(s.text for s in per_area[area]))] for area in areas}

        where: dict[str, list[str]] = {}
        for area in areas:
            for section in per_area[area]:
                where.setdefault(section.title, []).append(area)
        for title, places in where.items():
            if len(places) > 1:
                problems.append(f"the top-level heading {title!r} is in more than one place: "
                                + ", ".join(f"contract/{a}.md" for a in places))

        want = []
        for line in index_lines(current_order(root)):
            match = LINK.match(line)
            want.append((match.group("area"), match.group("anchor"), len(match.group("indent")) // 2 + 1,
                         match.group("label").replace("\\[", "[").replace("\\]", "]")))
        have = read_index(root)
        if have != want:
            missing = [w for w in want if w not in have]
            extra = [h for h in have if h not in want]
            detail = []
            for area, anchor, _, label in missing[:10]:
                detail.append(f"  not in the index: {label!r} (contract/{area}.md#{anchor})")
            for area, anchor, _, label in extra[:10]:
                detail.append(f"  in the index, not in the files: {label!r} (contract/{area}.md#{anchor})")
            if not detail:
                detail.append("  the same headings, in another order or at another depth")
            problems.append("the index in studio2/CONTRACT.md and studio2/contract/ disagree; "
                            "run contract_split.py --index:\n" + "\n".join(detail))
        all_heads = [h for area in areas for h in heads[area]]
        all_body = "\n".join(bodies.values())
    else:
        text = contract_file(root).read_text(encoding="utf-8")
        all_heads = [h.text for h in headings(text)]
        all_body = text
        heads, bodies = {}, {}
        unknown = [s.title for s in sections(text) if s.title and s.title not in AREA_OF]
        if unknown:
            out(f"note: {len(unknown)} top-level headings are not in AREA_OF yet; the split will stop on them: "
                + "; ".join(unknown))

    found = references(root)
    bad = []
    for path, line, area, name in found:
        if area is not None:
            if area not in AREAS:
                bad.append(f"  {path}:{line}: contract/{area}.md is not an area file")
                continue
            if not split_now or area not in bodies:
                bad.append(f"  {path}:{line}: contract/{area}.md does not exist")
                continue
            ok = resolves(name, heads[area], bodies[area])
        else:
            ok = resolves(name, all_heads, all_body)
        if not ok:
            bad.append(f"  {path}:{line}: {name!r}")
    if bad:
        problems.append("quoted section names that are no longer in the contract (rename the quote, "
                        "or put the heading back):\n" + "\n".join(bad))

    form = "split into studio2/contract/" if split_now else "one file (not split yet)"
    if problems:
        out(f"contract check FAILED ({form}):")
        for problem in problems:
            out("- " + problem)
        return 1
    out(f"contract check passed ({form}): {len(found)} quoted section names in "
        f"{len({p for p, *_ in found})} files all found, {len(all_heads)} headings"
        + (", the index matches the area files." if split_now else "."))
    return 0


# ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="check the contract and the references to it")
    group.add_argument("--index", action="store_true", help="rebuild the index from studio2/contract/")
    parser.add_argument("--root", type=Path, default=None, help="the repository (default: this one)")
    args = parser.parse_args(argv)
    root = (args.root or root or ROOT).resolve()
    try:
        if args.check:
            return check(root)
        if args.index:
            code = rebuild_index(root)
            return code or check(root)
        code = split(root)
        return code or check(root)
    except SplitError as error:
        print(f"contract_split.py: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
