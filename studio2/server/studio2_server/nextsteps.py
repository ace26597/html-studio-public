"""Suggested next steps (CONTRACT.md, "Suggested next steps").

After a request that completed, the chat shows a few chips: the studio's own (from what the request
did, decided here with no model call) and the agent's (proposed through `suggest_next_steps` in the
request it was already running). A chip only fills the chat box; nothing here sends anything.
"""

from __future__ import annotations

from . import effort

MAX_AGENT, MAX_STEPS = 3, 5
LABEL_CAP, TEXT_CAP = 40, 400
CHECK_OPTIONS = {"depth": "standard", "verify": True, "check_only": True}
CHECK_LABEL = "Verify content"
LOOK_OPTIONS = {"depth": "standard", "verify": False, "polish": True}
LOOK_LABEL = "Polish the look"


# What the agent is told, with the project facts of every message. The agent program loads the studio's
# tools on demand, so a rule kept only in the tool's description is never read: it has to be here.
AGENT_RULE = (
    "- When a request made or changed the page, end it by calling `mcp__studio__suggest_next_steps` once, as "
    "your last tool call, with one to three things the person might ask for next: steps [{label, text}]. "
    "`label` is a few words starting with a verb (\"Add a section on battery life\", 40 characters at most); "
    "`text` is the whole request as the person would write it, specific to this page, and something you could "
    "carry out yourself here. The person sees them as chips under your reply; a tap only fills their chat box. "
    "Suggest nothing already done, no questions, no check of the page's content and no general polish of its "
    "look (the studio offers those two itself); skip "
    "the call only when nothing sensible follows. Do not repeat the steps in your closing message.")


class StepsError(ValueError):
    pass


def _key(label: str) -> str:
    return " ".join(label.split()).casefold()


def agent_steps(args: dict) -> tuple[list[dict], int]:
    """The tool's arguments → ([{label, text}] kept, how many were cut for being over the limit).
    Empty and repeated ones are dropped without counting. Raises StepsError when `steps` is no list."""
    raw = (args or {}).get("steps")
    if not isinstance(raw, list):
        raise StepsError("steps must be a list of {label, text}.")
    steps, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        label, text = effort._cut(item.get("label"), LABEL_CAP), effort._cut(item.get("text"), TEXT_CAP)
        if not label or not text or _key(label) in seen:
            continue
        seen.add(_key(label))
        steps.append({"label": label, "text": text})
    return steps[:MAX_AGENT], max(0, len(steps) - MAX_AGENT)


def _items(count: int) -> str:
    return f"{count} item" if count == 1 else f"{count} items"


def studio_steps(facts: dict) -> list[dict]:
    """What the studio itself offers after a completed request: the first rule that applies, and
    beside the content check (or by itself) the polish of the look. `facts`: changed, has_page,
    verify, reported, failed, still_wrong, page_errors, polish, can_look (CONTRACT.md).

    This is the one place that decides which chip follows which outcome; change the rules here and
    the table in CONTRACT.md together.
    """
    still_wrong, failed = int(facts.get("still_wrong") or 0), int(facts.get("failed") or 0)
    page_errors = int(facts.get("page_errors") or 0)
    if page_errors > 0:     # a page that is broken when it loads comes before any check of its figures
        return [{"id": "errors", "from": "studio", "label": "Fix the page's errors",
                 "text": f"The page shows {page_errors} error{'s' if page_errors != 1 else ''} when it loads "
                         "(listed in the notice above). Find the cause, fix it, and look at the page again to "
                         "make sure it loads clean."}]
    if still_wrong > 0:
        return [{"id": "fix", "from": "studio", "label": "Fix what the check still finds",
                 "text": f"Fix the {_items(still_wrong)} the studio's own check still finds wrong on this page "
                         "(they are listed on the check card), then run the check again."}]
    if facts.get("reported") and failed > 0:
        return [{"id": "failed", "from": "studio", "label": "Work through the failed items",
                 "text": f"Work through the {_items(failed)} the check marked as failed: fix those a better "
                         "source settles, and tell me which ones need my decision."}]
    steps = []
    if facts.get("has_page") and not facts.get("reported") and (facts.get("changed") or facts.get("verify")):
        steps.append({"id": "check", "from": "studio", "label": CHECK_LABEL, "text": effort.VERIFY_ONLY_TEXT,
                      "options": dict(CHECK_OPTIONS)})
    # Never beside a step that fixes something: a page with errors or wrong figures is put right first.
    if facts.get("has_page") and facts.get("changed") and facts.get("can_look") and not facts.get("polish"):
        steps.append({"id": "look", "from": "studio", "label": LOOK_LABEL, "text": effort.POLISH_TEXT,
                      "options": dict(LOOK_OPTIONS)})
    return steps


def compose(facts: dict, proposed: list[dict] | None) -> list[dict]:
    """The `next_steps` event's `steps`: the studio's first, then the agent's (a1, a2, a3), five at most."""
    steps = studio_steps(facts)
    taken = {_key(step["label"]) for step in steps}
    for item in proposed or []:
        if _key(item["label"]) in taken:
            continue
        taken.add(_key(item["label"]))
        number = sum(1 for step in steps if step["from"] == "agent") + 1
        steps.append({"id": f"a{number}", "from": "agent", "label": item["label"], "text": item["text"]})
    return steps[:MAX_STEPS]
