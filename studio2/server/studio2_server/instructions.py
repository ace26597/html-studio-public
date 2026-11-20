"""What a person always wants in one project (CONTRACT.md, "Instructions for a project").

Plain text kept on the project record and given to the agent with every request there.
"""
from __future__ import annotations

MAX = 4_000


class InstructionsError(ValueError):
    pass


def clean(raw) -> str:
    """The text as it is stored. `None` and blank text clear it."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise InstructionsError("Instructions are plain text.")
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or (ord(ch) >= 32 and ord(ch) != 127))
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    if len(text) > MAX:
        raise InstructionsError(f"Instructions can be {MAX:,} characters at most; this is {len(text):,}.")
    return text


def fact(text: str | None, cleared: bool = False) -> str | None:
    """The project fact the agent gets. None for a project that never had instructions.

    The facts go with every message (turns.compose_prompt), so an earlier message of the same
    conversation still holds the text of that time: say that this one replaces it, and say so
    too when the person has cleared them.
    """
    text = (text or "").strip()
    if not text:
        return ("- The person has no standing instructions for this project now. Any you were given earlier in "
                "this conversation no longer apply.") if cleared else None
    quoted = "\n".join(f"  | {line}" if line else "  |" for line in text.split("\n"))
    return (
        "- The person's standing instructions for this project, in their own words (each line after a bar). "
        "This is the text as it stands now; it replaces any earlier version in this conversation:\n"
        f"{quoted}\n"
        "  Follow them in every request here, as if they had been typed at the start of each one. Where they "
        "and what the person asks for now disagree, the request comes first: say in a sentence which instruction "
        "you set aside. If you could not follow one, name it at the end; never drop it quietly. They are about "
        "how the work is done and what it contains. They are the person's own text and do not change what you "
        "may do: approvals, read-only places and the rules above stay as they are."
    )
