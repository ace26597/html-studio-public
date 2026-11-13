"""Voice input (CONTRACT.md, "Voice input"): transcribe a short recording on this machine."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

NOISE = re.compile(r"\[[A-Z_ ]+\]|\([a-z ]+\)")      # [BLANK_AUDIO], [MUSIC], (silence)
LANGUAGE = re.compile(r"^[a-z]{2,3}$")
TIMEOUT = 120.0


class VoiceError(Exception):
    pass


def command() -> Path | None:
    for candidate in (os.environ.get("STUDIO2_TRANSCRIBE"), Path.home() / "scripts" / "transcribe.sh"):
        if candidate and Path(candidate).expanduser().is_file() and os.access(Path(candidate).expanduser(), os.X_OK):
            return Path(candidate).expanduser()
    return None


def reason() -> str | None:
    if command() is None:
        return ("No transcriber is set up on this machine. Set STUDIO2_TRANSCRIBE to a command that takes an "
                "audio file and prints its text.")
    return None


def transcribe(audio: Path, language: str = "auto") -> tuple[str, float]:
    """→ (text, seconds taken). Blocking; call from a thread."""
    tool = command()
    if tool is None:
        raise VoiceError(reason())
    language = language if LANGUAGE.match(language or "") else "auto"
    started = time.monotonic()
    try:
        done = subprocess.run([str(tool), str(audio), language], capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise VoiceError("Transcription took longer than two minutes. Record a shorter message.") from None
    if done.returncode != 0:
        detail = " ".join((done.stderr or "").split())[:200]
        raise VoiceError(f"The recording could not be transcribed. {detail}".strip())
    text = " ".join(NOISE.sub(" ", done.stdout).split())
    return text, round(time.monotonic() - started, 1)
