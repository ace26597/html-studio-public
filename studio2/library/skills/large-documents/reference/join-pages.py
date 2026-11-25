"""Join the pieces of a long HTML report, or the pages of a site, from build/outline.json.

Usage: python join-pages.py <project folder> [--only <id>]

It reads build/outline.json, data/facts.json (the ledger) and sources/index.json, and writes:
- kind "report": one page (`page`, default index.html) from `shell`, a whole page holding
  <!-- join:toc --> (a jump menu) and <!-- join:sections --> (every section, in the outline's order);
- kind "site": every page in `pages`, each the `shell` with <!-- join:title -->, <!-- join:nav -->
  (the site menu, the current page marked) and <!-- join:content --> (that page's module).
Everywhere, {{fact:<key>}} becomes the ledger's figure with its check_page mark, <!-- join:sources -->
the sources cited on that page, {{root}} the way back to the project's root ("" or "../") and
{{check}} the address of the page's saved check (checks/<page>.json). It stops, writing nothing,
when a key is not in the ledger or is in it twice, a module is missing, a path leaves the
project (absolute, `..`, or through a link), or a section lacks its id. --only <id> builds that one piece into the system's temporary folder and prints where. Standard library only.
"""

import html
import json
import os
import posixpath
import re
import sys
import tempfile

TOKEN = re.compile(r"\{\{\s*fact:([A-Za-z0-9_.-]+)\s*\}\}")
CITED = re.compile(r'data-src="([^"]*)"')


def inside(root, path):
    """The real path of `path` under `root`, or None when it is absolute or leaves `root` (also through a link)."""
    if not isinstance(path, str) or not path or os.path.isabs(path) or "\0" in path:
        return None
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, path))
    return full if full.startswith(base + os.sep) else None


def load(root, path, default=None):
    full = inside(root, path)
    if full is None:
        if not os.path.lexists(os.path.join(root, path)) and default is not None:
            return default
        sys.exit(f"join-pages: {path} leads outside the project; nothing was written.")
    try:
        with open(full, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        if default is not None:
            return default
        sys.exit(f"join-pages: {path} is missing.")
    except ValueError as exc:
        sys.exit(f"join-pages: {path} is not valid JSON ({exc}).")


def shown(fact):
    if fact.get("display"):
        return str(fact["display"])
    value = fact.get("value")
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def sources_of(key, facts, seen=None):
    """The source ids and data files a fact rests on, through the facts it is computed from."""
    seen = seen if seen is not None else set()
    fact = facts.get(key) or {}
    if key in seen:
        return []
    seen.add(key)
    if fact.get("obtained") == "computed":
        found = []
        for name in fact.get("from") or []:
            found += [s for s in sources_of(name, facts, seen) if s not in found]
        return found
    return [str(fact["source"])] if fact.get("source") and fact.get("obtained") in ("quoted", "data") else []


def mark(key, facts, problems):
    fact = facts.get(key)
    if fact is None:
        problems.append(f"{{{{fact:{key}}}}} is not in data/facts.json")
        return f"[missing figure: {key}]"
    text, how = html.escape(shown(fact)), fact.get("obtained")
    label = html.escape(" ".join(str(fact.get(k) or "") for k in ("label", "period")).strip(), quote=True)
    if how == "quoted":
        if not fact.get("quote"):
            problems.append(f"{key} is quoted but has no quote in the ledger")
        attrs = f'data-src="{html.escape(str(fact.get("source")), quote=True)}" data-quote="{html.escape(str(fact.get("quote") or ""), quote=True)}"'
    elif how == "data":
        if not fact.get("sql"):
            problems.append(f"{key} comes from a data file but has no sql (one value) in the ledger")
        attrs = f'data-src="{html.escape(str(fact.get("source")), quote=True)}" data-sql="{html.escape(str(fact.get("sql") or ""), quote=True)}"'
    elif how == "computed":
        used = sources_of(key, facts)
        inputs = ", ".join(f"{name} = {shown(facts[name])}" for name in fact.get("from") or [] if name in facts)
        derived = f"{fact.get('formula')} with {inputs}" if inputs else str(fact.get("formula"))
        attrs = f'data-src="{html.escape(" ".join(used), quote=True)}" data-derived="{html.escape(derived, quote=True)}"'
    else:     # given by the person: nothing to check it against, and the page says so
        attrs = 'data-nocheck'
        label = (label + " (given by the person)").strip()
    return f'<span class="fact" {attrs} title="{label}">{text}</span>'


def source_list(text, sources):
    cited = []
    for value in CITED.findall(text):
        for name in value.split():
            if re.fullmatch(r"s\d+", name) and name not in cited:
                cited.append(name)
    if not cited:
        return ""
    rows = []
    for name in sorted(cited, key=lambda s: int(s[1:])):
        item = sources.get(name, {})
        title = html.escape(str(item.get("title") or name))
        url = item.get("url")
        if not (isinstance(url, str) and re.match(r"https?://", url, re.I)):
            url = None          # only a web address becomes a link (never javascript: or a file)
        link = f'<a href="{html.escape(url, quote=True)}">{title}</a>' if url else title
        saved = html.escape(str(item.get("fetched_at") or "")[:10])
        rows.append(f'<li id="src-{name}" value="{name[1:]}">{link}{f" (saved {saved})" if saved else ""}</li>')
    return '<ol class="sources">\n' + "\n".join(rows) + "\n</ol>"


def read(root, path, problems):
    full = inside(root, path)
    if full is None:
        problems.append(f"{path!r} is not a file inside the project; every module and shell must be")
        return ""
    try:
        with open(full, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        problems.append(f"{path} is missing")
        return ""


def fill(text, path, facts, sources, problems):
    """Facts, the source list, {{root}} and {{check}} for the page written at `path`."""
    depth = path.count("/")
    root = "../" * depth
    check = root + "checks/" + re.sub(r"[^A-Za-z0-9._-]+", "-", path) + ".json"
    text = TOKEN.sub(lambda m: mark(m.group(1), facts, problems), text)
    text = text.replace("{{root}}", root).replace("{{check}}", check)
    return text.replace("<!-- join:sources -->", source_list(text, sources))


def main(argv):
    if not argv or argv[0].startswith("-"):
        sys.exit(__doc__.split("\n\n")[1])
    root, only = argv[0], argv[argv.index("--only") + 1] if "--only" in argv else None
    outline = load(root, "build/outline.json")
    listed = [f for f in load(root, "data/facts.json", {"facts": []}).get("facts", []) if "key" in f]
    facts = {f["key"]: f for f in listed}
    sources = {s["id"]: s for s in load(root, "sources/index.json", {"sources": []}).get("sources", []) if "id" in s}
    problems, out = [], {}
    by_key = {}
    for fact in listed:
        by_key.setdefault(fact["key"], []).append(fact)
    for key, twice in by_key.items():   # one figure, one value: a key listed twice is what the ledger exists to stop
        if len(twice) > 1:
            values = ", ".join(f"{shown(f)} from {f.get('source') or f.get('obtained')}" for f in twice)
            problems.append(f"{key} is in data/facts.json {len(twice)} times ({values}): keep one")
    shell = read(root, outline.get("shell") or "build/shell.html", problems)
    if outline.get("kind") == "site":
        pages = outline.get("pages") or []
        if shell and "<!-- join:content -->" not in shell:
            problems.append("the shell has no <!-- join:content --> line")
        for entry in pages:
            if only and entry.get("id") != only:
                continue
            here = posixpath.dirname(entry["path"]) or "."
            links = []
            for other in pages:
                href = posixpath.relpath(other["path"], here)
                current = ' aria-current="page"' if other is entry else ""
                label = html.escape(other.get("nav") or other.get("title") or other["path"])
                links.append(f'<a href="{html.escape(href, quote=True)}"{current}>{label}</a>')
            text = (shell.replace("<!-- join:title -->", html.escape(entry.get("title") or entry["path"]))
                    .replace("<!-- join:nav -->", '<nav class="site-nav" aria-label="Site">' + "".join(links) + "</nav>")
                    .replace("<!-- join:content -->", read(root, entry["module"], problems).strip()))
            out[entry["path"]] = fill(text, entry["path"], facts, sources, problems)
    else:
        parts, toc = [], []
        for section in outline.get("sections") or []:
            if only and section.get("id") != only:
                continue
            text = read(root, section["module"], problems)
            if text and f'id="{section["id"]}"' not in text:
                problems.append(f'{section["module"]} has no element with id="{section["id"]}"')
            parts.append(text.strip())
            toc.append(f'<a href="#{html.escape(section["id"], quote=True)}">{html.escape(section.get("title") or section["id"])}</a>')
        if shell and "<!-- join:sections -->" not in shell:
            problems.append("the shell has no <!-- join:sections --> line")
        page = outline.get("page") or "index.html"
        text = shell.replace("<!-- join:toc -->", '<nav class="toc" aria-label="On this page">' + "".join(toc) + "</nav>")
        out[page] = fill(text.replace("<!-- join:sections -->", "\n\n".join(parts)), page, facts, sources, problems)
    if only and not out:
        sys.exit(f"join-pages: there is no piece with the id {only} in build/outline.json.")
    for path in out:                 # every page is written inside the project (or the temporary folder)
        if inside(root, path) is None:
            problems.append(f"the page path {path!r} is not inside the project")
    if problems:
        sys.exit("join-pages: nothing was written.\n- " + "\n- ".join(dict.fromkeys(problems)))
    target = tempfile.mkdtemp(prefix="join-pages-") if only else root
    for path, text in out.items():
        full = inside(target, path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(text)
    marks = sum(text.count('class="fact"') for text in out.values())
    print(f"join-pages: wrote {len(out)} page(s) to {target}: {', '.join(out)}; {marks} marked figures.")


if __name__ == "__main__":
    main(sys.argv[1:])
