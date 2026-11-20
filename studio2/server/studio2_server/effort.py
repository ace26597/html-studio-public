"""How much a request does (depth), whether its result is checked (verify), and whether the agent
asks the person first (ask).

The wording the agent receives lives here and only here, so it can be tuned in one place.
Standard with verify off adds nothing: the composed message is what it was before this step.
"""

from __future__ import annotations

import re

DEPTHS = ("quick", "standard", "deep")
DEFAULT = {"depth": "standard", "verify": False}
KEYS = {"depth", "verify", "ask"}          # the remembered choice; a request may also say check_only or polish
ONCE = {"check_only", "polish"}            # true for one request only, never remembered
DEEP_SKILL = "studio:deep-research"
NOT_REPORTED = "Verification was asked for and was not reported."

QUICK = (
    "The person chose Quick for this request: do the smallest thing that fully answers it. Use few "
    "sources (only as many as the answer needs), start no sub-agents, keep the result short and plain, "
    "and do not add sections, features or polish nobody asked for. Still mark every figure you state "
    "with its source.")

DEEP = (
    "The person chose Deep for this request: they want a more complete and thorough result and accept "
    "that it takes longer and uses more.\n"
    "- Plan first: write down what the result must cover before you build it.\n"
    "- For research, reports and dashboards, work the deep-research way (the deep-research skill): "
    "parallel researcher sub-agents, many more sources, cross-check important figures in more than one "
    "source, and include a section on what could not be confirmed.\n"
    "- For an app, build a more complete set of features, handle the edge cases (empty, wrong and very "
    "large input, errors from the back end), and exercise the main paths with look_at_page.\n"
    "- Make the result longer and more complete where the subject has more to say; do not pad.\n"
    "- At the end, have the work checked independently (the checker sub-agent) and fix what it finds.")

VERIFY = (
    "The person asked for the result to be verified. After your work in this request (or, when the "
    "request only asks for a check, instead of any new content), verify everything the person can see "
    "on the project's page(s) and in its files. Start the checker sub-agent for this, so the check is done in an "
    "independent context; give it the page paths, the path of every Office or PDF file the page shows or this "
    "request made, and this list:\n"
    "- every number against its saved source (check_page);\n"
    "- every figure in each file against the pages, data and sources (check_file);\n"
    "- every derived number recomputed from its inputs, in pages and files (formulas too); totals and "
    "percentages add up;\n"
    "- every chart matches its data table; units, dates and currencies are consistent;\n"
    "- statements in the text agree with the numbers and with each other;\n"
    "- for an app: the main paths exercised with look_at_page, and the values shown compared with what "
    "the back end returns.\n"
    "Fix what fails when the fix is certain; otherwise list it. Drop nothing silently. After your fixes "
    "and before you report, run check_page again on every page you changed and check_file on every file you changed: report an item as fixed only "
    "when that re-check shows it passing (otherwise it is failed), and make sure every source the page "
    "cites has an entry in its source list. Then call report_verification exactly once with the counts "
    "(checked, passed, fixed, failed, unverifiable) and one finding per item that was fixed, failed or "
    "could not be checked (passed items may be summarised). Say plainly that a pass means the figure is "
    "in the saved source or was recomputed from the page's own data, not that the source is right. "
    "After you report, the studio runs check_page and check_file once more itself and shows the person "
    "anything they still find wrong.\n"
    "In your closing message do not restate the counts or list the findings again: the person sees "
    "them on the check card. Point to the card and say only what needs their attention or decision.")

VERIFY_ONLY_TEXT = ("Verify content: check every figure and statement on this page against its sources, and "
                    "change nothing except certain fixes.")

POLISH = (
    "The person asked for the look of the page to be polished. This request is about how the page looks and "
    "reads, not what it says: add no content and remove none, and keep the page's own style (its palette, "
    "typefaces and layout idea). Make what is there look finished.\n"
    "1. Look at every page of the project with look_at_page at width 1440 first (a desktop: where the page is "
    "mostly read, and where it has to look best), then 820 (a tablet), then 390 (a phone). When a look's "
    "`layout.dark_styles` is true, look once at 1440 with dark: true as well. For an app, also look at its "
    "main states (use `actions`).\n"
    "2. For each look, open every picture in `tiles` with the Read tool (the single `image` is too small to "
    "judge a tall page) and read the `layout` list. That list is what the studio measured in the browser: "
    "confirm an item in the pictures before acting on it, since it can be intended.\n"
    "3. Judge the page as its reader would, the desktop view most carefully: it should look finished and use "
    "the width well, not like a phone page stretched out or a thin column in empty space. Text that is cut off, half hidden, overlapping or running off the "
    "screen; a page that scrolls sideways; padding and margins that are missing or uneven; things that do not "
    "line up; lines of text that are cramped or far too long; type too small or too faint to read; headings "
    "that do not stand out in order; tables, charts and pictures that do not fit their space; controls too "
    "small to tap; empty or broken areas; anything that looks unfinished.\n"
    "4. Fix what is clearly a defect (cut-off or overlapping text, overflow, broken or missing spacing, "
    "unreadable type, a layout that wastes or overfills a desktop window) with the smallest change that does "
    "it, in the page's own style. Desktop comes first, the tablet second; on a phone fix what is broken "
    "(sideways scrolling, cut-off or unreadable content) and never trade the desktop view for it. Never fix by "
    "hiding or shortening content. What is a matter of taste (another layout, palette or "
    "typeface, a restructure) do not change: suggest it.\n"
    "5. Look again at the same widths after your fixes; make sure each one worked and nothing else moved. If "
    "one did not work, say so.\n"
    "If look_at_page is not available, say that you cannot see the page in this studio and change nothing: do "
    "not polish blind.\n"
    "End with a short message: what you fixed (one line each, saying where), what you looked at (pages, widths, "
    "dark), and anything you could not check. Put what you only suggest in suggest_next_steps (one step each, "
    "three at most) instead of a long list.")

ASK = (
    "The person switched on \"Ask me first\": they would rather answer a question than have you decide "
    "quietly. Before any other work in this request, work out whether something you do not know would change "
    "what you make: who the result is for and what they do with it, what it should focus on or leave out, how "
    "it should be laid out or shown (a page, a table, charts, an app), which data, source or period to use, how "
    "long or detailed it should be.\n"
    "- Look first at the request, the person's standing instructions for this project, its context and the "
    "page as it is. Never ask what they already answer, what you can look up, or for permission to go ahead.\n"
    "- When something that matters is open, call `mcp__studio__ask_person` once, before you build anything, "
    "with one to four questions: {questions: [{question, choices?: [{label, description}], multiple?}], why?}. "
    "Give two to five concrete choices wherever choices make sense, put the one you would pick first and say "
    "in its description why, and set `multiple` when more than one can apply. The person can always answer in "
    "their own words instead, so do not add an \"Other\" choice. `why` is one sentence on what the answers "
    "change. The request waits on a card until they answer.\n"
    "- When the request is already specific (a small edit, a fix, a selected element, a follow-up to answers "
    "you already have), ask nothing and start.\n"
    "- A question they leave empty, or a skip, means: decide yourself. Ask a second time in the same request "
    "only when an answer opened a choice you cannot settle; never a third.\n"
    "- In your closing message, name in one line each what you assumed for anything you decided without "
    "asking. Do not repeat their answers back to them.")

POLISH_TEXT = ("Polish the look: see how this page looks on a desktop first, then on a tablet and a phone, fix what "
               "is clearly off, and suggest the rest.")


class OptionsError(ValueError):
    pass


def clean(raw, request: bool = True) -> dict:
    """A request's (or, with request=False, a project's) options → {depth, verify} (and `ask: True`
    when the person wants to be asked first), plus
    `check_only: True` for a request that only checks the page or `polish: True` for one about
    how it looks. Raises OptionsError on anything unknown."""
    if raw is None:
        return dict(DEFAULT)
    if not isinstance(raw, dict):
        raise OptionsError("options must be an object.")
    unknown = set(raw) - KEYS - (ONCE if request else set())
    if unknown:
        raise OptionsError(f"Unknown option: {', '.join(sorted(unknown))}.")
    depth = raw.get("depth", "standard")
    if depth is None:
        depth = "standard"
    if depth not in DEPTHS:
        raise OptionsError("depth must be quick, standard or deep.")
    verify = raw.get("verify", False)
    if verify is None:
        verify = False
    if not isinstance(verify, bool):
        raise OptionsError("verify must be true or false.")
    check_only = raw.get("check_only", False)
    if check_only is None:
        check_only = False
    if not isinstance(check_only, bool):
        raise OptionsError("check_only must be true or false.")
    polish = raw.get("polish", False)
    if polish is None:
        polish = False
    if not isinstance(polish, bool):
        raise OptionsError("polish must be true or false.")
    ask = raw.get("ask", False)
    if ask is None:
        ask = False
    if not isinstance(ask, bool):
        raise OptionsError("ask must be true or false.")
    if check_only and polish:
        raise OptionsError("check_only and polish cannot both be set: send them as two requests.")
    if check_only:     # a request that only checks the page is a verification
        return {"depth": depth, "verify": True, "check_only": True}
    if polish:
        return {"depth": depth, "verify": verify, "polish": True}
    # `ask` is there only when it is on, so a request without it is recorded exactly as before.
    return {"depth": depth, "verify": verify, **({"ask": True} if ask else {})}


def block(options: dict | None) -> str:
    """→ the `<studio-effort>` block for the composed message, or "" for standard without verify."""
    options = options or DEFAULT
    parts = []
    if options.get("depth") == "quick":
        parts.append(QUICK)
    elif options.get("depth") == "deep":
        parts.append(DEEP)
    if options.get("verify"):
        parts.append(VERIFY)
    if options.get("polish"):
        parts.append(POLISH)
    if options.get("ask") and not options.get("check_only") and not options.get("polish"):
        parts.insert(0, ASK)       # first: it is about what happens before the work
    return "<studio-effort>\n" + "\n\n".join(parts) + "\n</studio-effort>" if parts else ""


# -- report_verification --------------------------------------------------------------------
STATUSES = ("failed", "unverifiable", "fixed", "passed")
MAX_FINDINGS, WHAT_CAP, NOTE_CAP, SUMMARY_CAP = 60, 200, 400, 400


def _cut(value, limit: int) -> str:
    """White space folded; longer than `limit`: cut after the last sentence, else after the last
    whole word, within the room (sentences only in its second half), and ended with "…". Mid-word
    only when a single word fills the room."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    room = text[:limit - 2]                       # leaves space for " …"
    whole = text[len(room)] == " "                # the room happens to end at a word's end
    ends = [m.end() for m in re.finditer(r"[.!?][)\"'’”]?(?= |$)", room if whole else room + "x")
            if m.end() >= limit // 2 and m.end() <= len(room)]
    if ends:
        return room[:ends[-1]] + " …"
    cut = room if whole else room[:room.rfind(" ")] if " " in room else room
    return cut.rstrip(" ,;:–—-") + "…"


def _count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise OptionsError("checked, passed, fixed, failed and unverifiable must be whole numbers, 0 or more.")
    return int(value)


def verification_event(args: dict) -> dict:
    """The tool's arguments → the `verification` event's fields, sizes capped. Raises OptionsError."""
    counts = {key: _count(args.get(key, 0)) for key in ("checked", "passed", "fixed", "failed", "unverifiable")}
    raw = args.get("findings") or []
    if not isinstance(raw, list):
        raise OptionsError("findings must be a list.")
    findings = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        status = item.get("status")
        if status not in STATUSES:
            raise OptionsError("Each finding's status must be passed, fixed, failed or unverifiable.")
        findings.append({"what": _cut(item.get("what"), WHAT_CAP), "where": _cut(item.get("where"), WHAT_CAP),
                         "status": status, "note": _cut(item.get("note"), NOTE_CAP)})
    findings.sort(key=lambda f: STATUSES.index(f["status"]))     # failed first; stable otherwise
    kept = findings[:MAX_FINDINGS]
    return {**counts, "summary": _cut(args.get("summary"), SUMMARY_CAP), "findings": kept,
            "findings_total": len(findings), "findings_cut": len(findings) - len(kept)}
