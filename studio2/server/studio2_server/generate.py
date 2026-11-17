"""Photographs, clips and sound made by a generation service (CONTRACT.md, "Generated media").

One provider today: the Higgsfield command-line tool, signed in on this machine as the person.
Every generation spends that person's credits, so the studio asks first, every time, with the
price it was quoted, and keeps a record of what was spent.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

KINDS = {
    "image": {"model": "gpt_image_2", "types": (".png", ".jpg", ".jpeg", ".webp"), "wait": 6 * 60, "noun": "picture"},
    "video": {"model": "seedance_2_0", "types": (".mp4", ".mov", ".webm"), "wait": 25 * 60, "noun": "clip"},
    "audio": {"model": None, "types": (".mp3", ".wav", ".m4a", ".ogg", ".flac"), "wait": 10 * 60, "noun": "sound"},
}
MEDIA_FLAGS = {"references": "--image-references", "start_image": "--start-image", "end_image": "--end-image",
               "video_references": "--video-references", "audio_references": "--audio-references"}
HALT_SECONDS = 6 * 3600
DAILY_CREDITS = 100.0
SERVER_FAULT = ("http 500", "status 500", "500 internal", "internal server error", "status code 500",
                "502 bad gateway", "503 service", "status 502", "status 503")


class GenerateError(Exception):
    pass


def find_cli() -> str | None:
    override = os.environ.get("STUDIO2_HIGGSFIELD")
    if override:
        return override if Path(override).is_file() else None
    return shutil.which("higgsfield")


def reason() -> str | None:
    """None when generation can be offered; otherwise why not."""
    return None if find_cli() else ("No generation service is set up on this machine (the Higgsfield "
                                    "command-line tool, `higgsfield`, signed in).")


def flags(params: dict) -> list[str]:
    """{aspect_ratio: "16:9", generate_audio: False} → ["--aspect_ratio", "16:9", "--generate_audio", "false"]."""
    out: list[str] = []
    for name, value in params.items():
        if value is None or value == "":
            continue
        if not str(name).replace("_", "").isalnum():
            raise GenerateError(f"{name} is not a parameter name.")
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            value = json.dumps(value)
        out += [f"--{name}", str(value)]
    return out


def is_server_fault(text: str) -> bool:
    low = (text or "").lower()
    return any(mark in low for mark in SERVER_FAULT)


class Generator:
    def __init__(self, data_dir: Path, daily_credits: float = DAILY_CREDITS):
        self.state_file = data_dir / "generation.json"
        self.usage_file = data_dir / "generation-usage.jsonl"
        self.daily_credits = daily_credits
        self._lock = asyncio.Lock()          # one paid job at a time

    # -- the command-line tool --------------------------------------------------------------
    async def _cli(self, *args: str, timeout: float = 60) -> tuple[int, str, str]:
        cli = find_cli()
        if not cli:
            raise GenerateError(reason())
        process = await asyncio.create_subprocess_exec(
            cli, *args, "--json", "--no-color", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.TimeoutError:
            process.kill()
            raise GenerateError(f"The generation service did not answer within {timeout:.0f} seconds.") from None
        return process.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")

    async def _json(self, *args: str, timeout: float = 60):
        code, out, err = await self._cli(*args, timeout=timeout)
        text = " ".join((err or out).split())
        if code:
            if "login" in text.lower() or "unauthor" in text.lower() or "401" in text:
                raise GenerateError("The generation service is signed out on this machine. The person signs in "
                                    "again with `higgsfield auth login`; you cannot do that for them.")
            raise GenerateError(text[:600] or "The generation service refused the request.")
        try:
            return json.loads(out)
        except ValueError:
            raise GenerateError(f"The generation service gave an answer that could not be read: {out[:200]}") from None

    async def balance(self) -> float:
        return float((await self._json("account", "status")).get("credits") or 0)

    async def models(self, kind: str | None = None) -> list[dict]:
        listed = await self._json("model", "list")
        listed = listed if isinstance(listed, list) else listed.get("items") or listed.get("models") or []
        return [{"model": m.get("job_type"), "name": m.get("display_name"), "kind": m.get("type")}
                for m in listed if m.get("job_type") and (not kind or m.get("type") == kind)]

    async def model(self, name: str) -> dict:
        found = await self._json("model", "get", name)
        return {"model": found.get("job_type"), "name": found.get("display_name"), "kind": found.get("type"),
                "params": [{k: p.get(k) for k in ("name", "type", "default", "required", "enum") if p.get(k) is not None}
                           for p in found.get("params") or []]}

    async def cost(self, model: str, arguments: list[str]) -> float:
        quoted = await self._json("generate", "cost", model, *arguments, timeout=180)
        return float(quoted.get("credits") or 0)

    # -- the brake --------------------------------------------------------------------------
    def halted(self) -> str | None:
        """A server fault on the service's side can charge credits and deliver nothing, and a
        retry usually does the same. After one, generation stays off for six hours."""
        try:
            state = json.loads(self.state_file.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        until = float(state.get("halted_until") or 0)
        if until <= time.time():
            return None
        when = datetime.fromtimestamp(until).strftime("%H:%M")
        return (f"Generation is paused until {when} because the service failed with a server error "
                f"({state.get('why', '')[:160]}). A retry could spend credits and deliver nothing. Do not try "
                "another model; tell the person and carry on without generated media.")

    def halt(self, why: str) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({"halted_until": time.time() + HALT_SECONDS, "why": why[:300]}), "utf-8")

    def resume(self) -> None:
        self.state_file.unlink(missing_ok=True)

    # -- what was spent ---------------------------------------------------------------------
    def spent_today(self) -> float:
        today, total = datetime.now(timezone.utc).date().isoformat(), 0.0
        try:
            lines = self.usage_file.read_text("utf-8").splitlines()
        except OSError:
            return 0.0
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if str(row.get("at", "")).startswith(today):
                total += float(row.get("credits") or 0)
        return round(total, 2)

    def record(self, **row) -> None:
        self.usage_file.parent.mkdir(parents=True, exist_ok=True)
        with self.usage_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **row}) + "\n")

    async def _charged(self, before: float, quoted: float) -> tuple[float | None, float]:
        """→ (balance now, credits this job cost). The fall in the balance since just before the job
        was bought (no other studio job runs meanwhile); the quoted price when the balance cannot be read."""
        try:
            balance = await self.balance()
        except GenerateError:
            return None, quoted
        fall = round(before - balance, 2)
        # A balance that rose (a refund for an earlier job, the monthly credits) says nothing about
        # this job, and a negative row would loosen the daily limit: count the quoted price.
        return balance, (fall if fall >= 0 else quoted)

    @staticmethod
    async def _download(url: str) -> bytes:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=20), follow_redirects=True) as http:
            response = await http.get(url)
            response.raise_for_status()
            return response.content

    # -- one generation ---------------------------------------------------------------------
    async def quote(self, kind: str, model: str | None, params: dict, media: dict[str, list[Path]]) -> dict:
        """Check the request and ask the service what it would cost. Spends nothing."""
        if kind not in KINDS:
            raise GenerateError("kind must be image, video or audio.")
        model = model or KINDS[kind]["model"]
        if not model:
            raise GenerateError("Say which model to use for sound: call generation_models with kind: \"audio\" "
                                "to see them, then with `model` to see what one takes.")
        stopped = self.halted()
        if stopped:
            raise GenerateError(stopped)
        arguments = flags(params)
        for name, paths in media.items():
            for path in paths:
                arguments += [MEDIA_FLAGS[name], str(path)]
        credits = await self.cost(model, arguments)
        balance = await self.balance()
        if credits > balance:
            raise GenerateError(f"This would cost {credits:g} credits and the account has {balance:g}.")
        spent = self.spent_today()
        if spent + credits > self.daily_credits:
            raise GenerateError(f"This would cost {credits:g} credits; {spent:g} have been spent through the studio "
                                f"today and the studio's limit is {self.daily_credits:g} a day "
                                "(STUDIO2_GENERATE_DAILY_CREDITS).")
        return {"kind": kind, "model": model, "arguments": arguments, "credits": credits, "balance": balance}

    async def run(self, quoted: dict, target: Path, project_id: str, prompt: str) -> dict:
        """Make it, download it → {file (Path), credits, balance, job}. The quote was approved by the person."""
        kind = quoted["kind"]
        async with self._lock:
            # The quote was checked when it was made, but the person approves it later: other jobs
            # may have been bought since, or a server fault may have paused generation. Check again
            # here, inside the lock, so nothing bought in between is missed.
            stopped = self.halted()
            if stopped:
                raise GenerateError(stopped)
            spent = self.spent_today()
            if spent + quoted["credits"] > self.daily_credits:
                raise GenerateError(f"Nothing was spent. This would cost {quoted['credits']:g} credits; {spent:g} have "
                                    f"been spent through the studio today and the studio's limit is "
                                    f"{self.daily_credits:g} a day (STUDIO2_GENERATE_DAILY_CREDITS).")
            before = await self.balance()
            if quoted["credits"] > before:
                raise GenerateError(f"Nothing was spent. This would cost {quoted['credits']:g} credits and the "
                                    f"account now has {before:g}.")
            wait = KINDS[kind]["wait"]
            try:
                code, out, err = await self._cli("generate", "create", quoted["model"], *quoted["arguments"],
                                                 "--wait", "--wait-timeout", f"{wait // 60}m", timeout=wait + 60)
            except GenerateError as exc:
                if not find_cli():
                    raise           # the command was never run: nothing was asked for
                # The studio stopped waiting, but the service may still finish the job and charge for
                # it. The limit counts the quoted price, and the row says that is what it is.
                self.record(project=project_id, kind=kind, model=quoted["model"], credits=quoted["credits"],
                            credits_basis="quoted", prompt=prompt[:300], outcome="timed_out", error=str(exc)[:300])
                raise GenerateError(f"{exc} The job may still finish and be charged, so its quoted price "
                                    f"({quoted['credits']:g} credits) is counted against today's limit. Do not "
                                    "generate it again; tell the person.") from None
            said = " ".join((err or "").split()) or " ".join(out.split())[:400]
            jobs = []
            if not code:
                try:
                    parsed = json.loads(out)
                    jobs = parsed if isinstance(parsed, list) else [parsed]
                except ValueError:
                    pass
            job = next((j for j in jobs if j.get("result_url")), None)
            if job is None:
                status = (jobs[0].get("status") if jobs else None) or "failed"
                if is_server_fault(said):
                    self.halt(said)
                    self.record(project=project_id, kind=kind, model=quoted["model"], credits=quoted["credits"],
                                prompt=prompt[:300], outcome="server_fault")
                    raise GenerateError(self.halted() or "The generation service failed with a server error.")
                # A job that did not complete is usually not charged, but only the balance says so.
                _balance, spent = await self._charged(before, 0.0)
                self.record(project=project_id, kind=kind, model=quoted["model"], credits=spent, prompt=prompt[:300],
                            outcome=str(status))
                raise GenerateError(f"The generation did not complete ({status}). {said[:400]} "
                                    "Do not retry with another model. Change the request only if the message "
                                    "says what was wrong with it; otherwise tell the person.")
            url = str(job["result_url"])
            suffix = Path(url.split("?")[0]).suffix.lower()
            if suffix and suffix != target.suffix.lower() and not (
                    kind == "image" and suffix in KINDS["image"]["types"] and target.suffix.lower() in KINDS["image"]["types"]):
                target = target.with_suffix(suffix)
            # From here the service has made the job and charged for it, so a failure to fetch or
            # save it must still be recorded, or the daily limit would not count what was paid.
            step = "download_failed"
            try:
                data = await self._download(url)
                step = "save_failed"
                target.parent.mkdir(parents=True, exist_ok=True)
                if kind == "image" and suffix != target.suffix.lower():
                    import io

                    from PIL import Image
                    image = Image.open(io.BytesIO(data))
                    (image.convert("RGB") if target.suffix.lower() in (".jpg", ".jpeg") else image).save(target, quality=92)
                else:
                    target.write_bytes(data)
            except Exception as exc:
                balance, spent = await self._charged(before, quoted["credits"])
                self.record(project=project_id, kind=kind, model=quoted["model"], credits=spent, prompt=prompt[:300],
                            outcome=step, job=job.get("id"), error=str(exc)[:300])
                what = "downloaded" if step == "download_failed" else "saved into the project"
                raise GenerateError(f"The service made it and {spent:g} credits were spent, but the result could not "
                                    f"be {what} ({str(exc)[:200]}). Do not generate it again; tell the person.") from None
            balance, spent = await self._charged(before, quoted["credits"])
            self.record(project=project_id, kind=kind, model=quoted["model"], credits=spent, prompt=prompt[:300],
                        outcome="completed", job=job.get("id"), file=target.name)
            return {"file": target, "credits": spent, "balance": balance, "job": job.get("id"),
                    "width": (job.get("params") or {}).get("width"), "height": (job.get("params") or {}).get("height")}
