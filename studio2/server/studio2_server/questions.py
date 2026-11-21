"""The cards the agent puts to the person while it works: questions, its plan, a check-in on a part
(CONTRACT.md, "How much to involve the person").

The tool `ask_person` pauses the running request on a card, like an approval. This module is the
part with no waiting in it: what a call may ask, what an answer may say, and what the agent is told.
"""
from __future__ import annotations

import re

from . import effort

MAX_QUESTIONS, MAX_CHOICES, MAX_REFERENCES = 4, 6, 8
QUESTION_CAP, LABEL_CAP, DESCRIPTION_CAP, WHY_CAP, TEXT_CAP = 300, 80, 200, 300, 2_000
TITLE_CAP, SUMMARY_CAP, URL_CAP = 80, 1_500, 500
KINDS = ("questions", "plan", "checkpoint")
# A plan or a check-in that came without a question still asks one.
DEFAULT_QUESTION = {
    "plan": {"question": "Go ahead with this plan?", "choices": [
        {"label": "Go ahead", "description": ""},
        {"label": "Change something", "description": "Say what in your own words."}]},
    "checkpoint": {"question": "Carry on to the next part?", "choices": [
        {"label": "Looks good, carry on", "description": ""},
        {"label": "Change this part", "description": "Say what in your own words."}]},
}

OFF = ("The person set this request to run on its own: they do not want to be asked. Do not ask: decide "
       "yourself, carry on, and say in your closing message what you assumed.")
NOBODY = "No request is running, so there is nobody to ask. Decide yourself and say what you assumed."
TOO_MANY = ("This request has shown the person all the cards their setting allows. Do not ask again: work from "
            "what you know, finish the request, and say in your closing message what you assumed.")
SKIPPED_BEFORE = ("The person told you earlier in this request to go on by yourself. Do not ask again: decide "
                  "yourself and say in your closing message what you assumed.")
AFTER_ANSWER = ("Work from these answers. What the person typed in their own words outranks a choice. A question "
                "marked unanswered is yours to decide: say in your closing message what you assumed for it. Do not "
                "repeat the questions or the answers back; carry on with the work.")
AFTER_SKIP = ("The person told you to go on by yourself. Decide what is open, carry on with the request to its "
              "end, and say in your closing message what you assumed. Do not ask again in this request.")
PLAN_FIRST = (
    "Held back once, on purpose. The person set how much to involve them to {stop} of 100: they want to agree "
    "your plan before you build, and you have shown no plan in this request. A short or one-page piece still "
    "gets its plan. If this request is more than a small, specific edit, stop now and call "
    "`mcp__studio__ask_person` with kind \"plan\" (the plan in `summary`, what it rests on in `references`, one "
    "question on whether to go ahead), then build after they answer. If it really is a small edit, a fix, or a "
    "follow-up to a plan they already agreed in this conversation, make the same change again: it will go "
    "through this time.")
# The agent program has a question tool of its own; in the studio there is one card and one path.
BUILT_IN = "AskUserQuestion"
BUILT_IN_ON = ("Ask through the studio instead: call `mcp__studio__ask_person` with {kind?, title?, summary?, "
               "references?, questions: [{question, choices?: [{label, description}], multiple?}], why?}. The "
               "person answers on a card.")


class QuestionsError(ValueError):
    pass


def _key(label: str) -> str:
    return " ".join(label.split()).casefold()


PROMISE = re.compile(r"^\s*i\s*(?:'|\u2019)?\s*(?:ll|will)\b", re.I)     # "I'll paste it": no answer, a promise of one


def _choices(raw) -> list[dict]:
    kept, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        pick = False
        if isinstance(item, dict):
            label = effort._cut(item.get("label"), LABEL_CAP) if isinstance(item.get("label"), (str, int, float)) else ""
            description = effort._cut(item.get("description"), DESCRIPTION_CAP)
            pick = item.get("pick") is True
        else:
            label, description = effort._cut(item, LABEL_CAP) if isinstance(item, (str, int, float)) else "", ""
        if not label or _key(label) in seen or PROMISE.match(label):
            continue
        seen.add(_key(label))
        kept.append({"label": label, "description": description,
                     **({"pick": True} if pick and not any(choice.get("pick") for choice in kept) else {})})
    kept = kept[:MAX_CHOICES]
    return kept if len(kept) >= 2 else []      # one choice is no choice: the person answers in words


def _summary(raw) -> str:
    """Markdown the card shows: lines kept, each tidied, the whole cut at a line or a word."""
    if not isinstance(raw, str):
        return ""
    lines = [" ".join(line.split()) if not line.startswith((" ", "\t")) else line.rstrip()
             for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    text = "\n".join(lines).strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    if len(text) <= SUMMARY_CAP:
        return text
    room = text[:SUMMARY_CAP - 2]
    cut = room.rfind("\n") if room.rfind("\n") >= SUMMARY_CAP // 2 else room.rfind(" ")
    return (room[:cut] if cut > 0 else room).rstrip() + " …"


def _references(raw, workspace=None) -> list[dict]:
    """What a plan or a part rests on: a label, a note, and an address or a file in the project."""
    kept = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            item = {"label": item, "url": item} if item.lower().startswith(("http://", "https://")) else {"label": item}
        if not isinstance(item, dict):
            continue
        url = item.get("url") if isinstance(item.get("url"), str) else ""
        url = url.strip() if url.strip().lower().startswith(("http://", "https://")) and len(url.strip()) <= URL_CAP \
            and not any(ch.isspace() for ch in url.strip()) else ""
        path = ""
        if not url and isinstance(item.get("path"), str) and item["path"].strip() and workspace is not None:
            from . import workspace as ws
            try:
                target = ws.safe_path(workspace, item["path"].strip())
                if target.is_file():
                    path = target.relative_to(workspace.resolve()).as_posix()
            except Exception:  # noqa: BLE001 - not a file of the project: the reference keeps its words only
                path = ""
        named = item.get("label") if isinstance(item.get("label"), str) else ""
        label = effort._cut(named or path or url, LABEL_CAP)
        if not label:
            continue
        entry = {"label": label, "note": effort._cut(item.get("note"), DESCRIPTION_CAP)}
        if url:
            entry["url"] = url
        elif path:
            entry["path"] = path
        kept.append(entry)
    return kept[:MAX_REFERENCES]


def clean(args: dict, workspace=None) -> dict:
    """The tool's arguments → the card: {kind, title, summary, references, questions: [{id, question,
    multiple, choices}], why, cut} (`cut`: questions over the limit, not asked). `workspace`: the
    project's folder, for a reference that names a file in it. Raises QuestionsError when there is
    nothing to ask."""
    args = args if isinstance(args, dict) else {}
    kind = args.get("kind") if args.get("kind") in KINDS else "questions"
    raw = args.get("questions")
    if raw is None and kind in DEFAULT_QUESTION:
        raw = []
    if not isinstance(raw, list):
        raise QuestionsError("questions must be a list of {question, choices?, multiple?}.")
    asked = []
    for item in raw:
        if isinstance(item, str):
            item = {"question": item}
        if not isinstance(item, dict):
            continue
        text = effort._cut(item.get("question"), QUESTION_CAP)
        if not text:
            continue
        choices = _choices(item.get("choices"))
        asked.append({"question": text, "multiple": bool(choices) and item.get("multiple") is True,
                      "choices": choices})
    if not asked and kind in DEFAULT_QUESTION:
        default = DEFAULT_QUESTION[kind]
        asked.append({"question": default["question"], "multiple": False,
                      "choices": [dict(choice) for choice in default["choices"]]})
    if not asked:
        raise QuestionsError("questions must hold at least one {question}: the text of what you want to know.")
    kept = [{"id": f"q{number}", **item} for number, item in enumerate(asked[:MAX_QUESTIONS], 1)]
    return {"kind": kind, "title": effort._cut(args.get("title"), TITLE_CAP), "summary": _summary(args.get("summary")),
            "references": _references(args.get("references"), workspace), "questions": kept,
            "why": effort._cut(args.get("why"), WHY_CAP), "cut": len(asked) - len(kept)}


def check(questions: list[dict], body) -> list[dict] | None:
    """What the person sent → one {id, chosen, text} per question, in order; None for a skip.
    Raises QuestionsError (the 400's message) on anything the card could not have sent."""
    if not isinstance(body, dict):
        raise QuestionsError("Send {answers: [...]} or {skip: true}.")
    if body.get("skip") is True:
        return None
    raw = body.get("answers")
    if not isinstance(raw, list):
        raise QuestionsError("answers must be a list of {id, chosen?, text?}.")
    known = {item["id"]: item for item in questions}
    given: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in known:
            raise QuestionsError("An answer names a question this card does not have.")
        if item["id"] in given:
            raise QuestionsError("A question was answered twice.")
        question = known[item["id"]]
        chosen = item.get("chosen")
        chosen = [] if chosen is None else chosen
        labels = [choice["label"] for choice in question["choices"]]
        if not isinstance(chosen, list) or any(label not in labels for label in chosen):
            raise QuestionsError("chosen must be a list of that question's own choices.")
        chosen = [label for label in labels if label in chosen]      # the card's order, each once
        if len(chosen) > 1 and not question["multiple"]:
            raise QuestionsError("That question takes one choice.")
        text = item.get("text")
        text = "" if text is None else text
        if not isinstance(text, str):
            raise QuestionsError("text must be text.")
        text = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
        if len(text) > TEXT_CAP:
            raise QuestionsError(f"An answer can be {TEXT_CAP:,} characters at most; this is {len(text):,}.")
        given[item["id"]] = {"id": item["id"], "chosen": chosen, "text": text}
    answers = [given.get(item["id"]) or {"id": item["id"], "chosen": [], "text": ""} for item in questions]
    if not any(answer["chosen"] or answer["text"] for answer in answers):
        raise QuestionsError("Answer at least one question, or skip them.")
    return answers


def result(questions: list[dict], answers: list[dict] | None, cut: int = 0, left: int = 0) -> dict:
    """What the tool hands back to the agent. `left`: how many more cards this request may show."""
    if answers is None:
        return {"answered": False, "next": AFTER_SKIP}
    by_id = {answer["id"]: answer for answer in answers}
    told = []
    for question in questions:
        answer = by_id.get(question["id"]) or {"chosen": [], "text": ""}
        entry = {"question": question["question"], "chosen": answer["chosen"], "text": answer["text"]}
        if not answer["chosen"] and not answer["text"]:
            entry["unanswered"] = True
        told.append(entry)
    return {"answered": True, "answers": told, "cut": cut, "left": left,
            "next": AFTER_ANSWER + ("" if left else " That was the last card this request may show.")}
