"""Voice input (CONTRACT.md, "Voice input")."""

import stat


def transcriber(tmp_path, monkeypatch, body):
    script = tmp_path / "transcribe.sh"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("STUDIO2_TRANSCRIBE", str(script))


async def test_a_recording_becomes_text_without_noise_markers(client, tmp_path, monkeypatch):
    transcriber(tmp_path, monkeypatch, 'test -s "$1" || exit 3\necho "[BLANK_AUDIO] make the chart   bigger"\necho "language=$2"\n')
    assert (await client.get("/api/health")).json()["transcription"] == {"available": True, "reason": None}
    answer = await client.post("/api/transcribe", files={"file": ("clip.webm", b"audio-bytes", "audio/webm")},
                               data={"language": "en; rm -rf /"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["text"] == "make the chart bigger language=auto"      # an odd language value is not passed on


async def test_a_failed_or_missing_transcriber_says_so(client, tmp_path, monkeypatch):
    transcriber(tmp_path, monkeypatch, 'echo "bad audio" >&2\nexit 1\n')
    failed = await client.post("/api/transcribe", files={"file": ("clip.mp4", b"x", "audio/mp4")})
    assert failed.status_code == 422 and failed.json()["error"]["code"] == "transcription_failed"
    monkeypatch.setenv("STUDIO2_TRANSCRIBE", str(tmp_path / "missing.sh"))
    monkeypatch.setenv("HOME", str(tmp_path))
    gone = await client.post("/api/transcribe", files={"file": ("clip.mp4", b"x", "audio/mp4")})
    assert gone.status_code == 503 and gone.json()["error"]["code"] == "transcription_unavailable"
    assert (await client.get("/api/health")).json()["transcription"]["available"] is False
