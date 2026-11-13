"""How much a request does (depth) and whether its result is checked (verify).

The wording the agent receives lives here and only here, so it can be tuned in one place.
Standard with verify off adds nothing: the composed message is what it was before this step.
"""

from __future__ import annotations

import re

DEPTHS = ("quick", "standard", "deep")
DEFAULT = {"depth": "standard", "verify": False}
KEYS = {"depth", "verify"}                 # the remembered choice; a request may also say check_only
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
    "on the project's page(s). Start the checker sub-agent for this, so the check is done in an "
    "independent context; give it the page paths and this list:\n"
    "- every number against its saved source (check_page);\n"
    "- every derived number recomputed from its inputs; totals and percentages add up;\n"
    "- every chart matches its data table; units, dates and currencies are consistent;\n"
    "- statements in the text agree with the numbers and with each other;\n"
    "- for an app: the main paths exercised with look_at_page, and the values shown compared with what "
    "the back end returns.\n"
    "Fix what fails when the fix is certain; otherwise list it. Drop nothing silently. After your fixes "
    "and before you report, run check_page again on every page you changed: report an item as fixed only "
    "when that re-check shows it passing (otherwise it is failed), and make sure every source the page "
    "cites has an entry in its source list. Then call report_verification exactly once with the counts "
    "(checked, passed, fixed, failed, unverifiable) and one finding per item that was fixed, failed or "
    "could not be checked (passed items may be summarised). Say plainly that a pass means the figure is "
    "in the saved source or was recomputed from the page's own data, not that the source is right. "
    "After you report, the studio runs check_page once more itself and shows the person anything it "
    "still finds wrong.\n"
    "In your closing message do not restate the counts or list the findings again: the person sees "
    "them on the check card. Point to the card and say only what needs their attention or decision.")

VERIFY_ONLY_TEXT = "Check this page: verify everything on it, and change nothing except certain fixes."


class OptionsError(ValueError):
    pass


def clean(raw, request: bool = True) -> dict:
    """A request's (or, with request=False, a project's) options → {depth, verify}, plus
    `check_only: True` for a request that only checks the page. Raises OptionsError on anything
    unknown."""
    if raw is None:
        return dict(DEFAULT)
    if not isinstance(raw, dict):
        raise OptionsError("options must be an object.")
    unknown = set(raw) - KEYS - ({"check_only"} if request else set())
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
    if check_only:     # a request that only checks the page is a verification
        return {"depth": depth, "verify": True, "check_only": True}
    return {"depth": depth, "verify": verify}


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
