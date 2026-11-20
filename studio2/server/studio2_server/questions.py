"""Questions the agent asks the person before it works (CONTRACT.md, "Questions before the work").

The tool `ask_person` pauses the running request on a card, like an approval. This module is the
part with no waiting in it: what a call may ask, what an answer may say, and what the agent is told.
"""
from __future__ import annotations

from . import effort

MAX_QUESTIONS, MAX_CHOICES, MAX_ASKS = 4, 6, 2
QUESTION_CAP, LABEL_CAP, DESCRIPTION_CAP, WHY_CAP, TEXT_CAP = 300, 80, 200, 300, 2_000

OFF = ("The person has not switched on questions for this request. Do not ask: decide yourself, carry on, "
       "and say in your closing message what you assumed.")
NOBODY = "No request is running, so there is nobody to ask. Decide yourself and say what you assumed."
TOO_MANY = ("You have already asked twice in this request. Do not ask again: work from what you know and say "
            "in your closing message what you assumed.")
SKIPPED_BEFORE = ("The person skipped your questions earlier in this request. Do not ask again: decide yourself "
                  "and say in your closing message what you assumed.")
AFTER_ANSWER = ("Work from these answers. What the person typed in their own words outranks a choice. A question "
                "marked unanswered is yours to decide: say in your closing message what you assumed for it. Do not "
                "repeat the questions or the answers back; start the work.")
AFTER_SKIP = ("The person chose not to answer. Decide yourself, carry on with the request, and say in your closing "
              "message what you assumed. Do not ask again in this request.")
# The agent program has a question tool of its own; in the studio there is one card and one path.
BUILT_IN = "AskUserQuestion"
BUILT_IN_ON = ("Ask through the studio instead: call `mcp__studio__ask_person` with {questions: [{question, "
               "choices?: [{label, description}], multiple?}], why?}. The person answers on a card.")


class QuestionsError(ValueError):
    pass


def _key(label: str) -> str:
    return " ".join(label.split()).casefold()


def _choices(raw) -> list[dict]:
    kept, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            label = effort._cut(item.get("label"), LABEL_CAP)
            description = effort._cut(item.get("description"), DESCRIPTION_CAP)
        else:
            label, description = effort._cut(item, LABEL_CAP) if isinstance(item, (str, int, float)) else "", ""
        if not label or _key(label) in seen:
            continue
        seen.add(_key(label))
        kept.append({"label": label, "description": description})
    kept = kept[:MAX_CHOICES]
    return kept if len(kept) >= 2 else []      # one choice is no choice: the person answers in words


def clean(args: dict) -> tuple[list[dict], str, int]:
    """The tool's arguments → ([{id, question, multiple, choices}], why, how many were cut for being
    over the limit). Raises QuestionsError when nothing is left to ask."""
    raw = (args or {}).get("questions")
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
    if not asked:
        raise QuestionsError("questions must hold at least one {question}: the text of what you want to know.")
    kept = [{"id": f"q{number}", **item} for number, item in enumerate(asked[:MAX_QUESTIONS], 1)]
    return kept, effort._cut((args or {}).get("why"), WHY_CAP), len(asked) - len(kept)


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


def result(questions: list[dict], answers: list[dict] | None, cut: int = 0) -> dict:
    """What the tool hands back to the agent."""
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
    return {"answered": True, "answers": told, "cut": cut, "next": AFTER_ANSWER}
