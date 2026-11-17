"""The backup cost calculation (CONTRACT.md, "The backup calculation").

When a LiteLLM route sends no cost for a request, the studio works one out: token counts times the
model's rate. Rates come from the gateway's own price for the model, then from the list that ships
with the studio. A source is used only when it has a rate for every kind of token the request used;
otherwise the request stays unpriced. Nothing here guesses a rate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

KINDS = ("input", "output", "cache_read", "cache_write")
LONG_WRITE = "cache_write_1h"       # a cache entry written with the 1-hour lifetime: a rate of its own where it differs
MILLION = 1_000_000


def _rate(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _zero(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def last_part(name: str) -> str:
    """`openrouter/anthropic/claude-opus-5.5` and `claude-opus-5-5-20260401` → `claude-opus-5-5`.
    A `:free` ending stays: the free variant is not the paid model of that name."""
    tail = str(name or "").strip().lower().split("/")[-1]
    free = tail.endswith(FREE_ENDINGS[0])
    tail = re.sub(r"-\d{8}$", "", tail.split(":")[0].replace(".", "-"))
    return tail + FREE_ENDINGS[0] if free else tail


FREE_ENDINGS = (":free", "/free")       # OpenRouter's free variants, and its router to them


def free_model(model: str | None, sources: tuple = ()) -> bool:
    """A model its provider serves for free: its name says so, or a list of rates gives it 0 and 0
    (`shipped` marks those; a gateway's own 0 and 0 is a plan route's, so `from_gateway` never does)."""
    name = str(model or "").strip().lower()
    if not name:
        return False
    if name.endswith(FREE_ENDINGS):
        return True
    return any(bool((source.find(name) or {}).get("free")) for source in sources if source)


def free_request(model: str | None, reported_cost, sources: tuple = ()) -> bool:
    """Whether a request whose route reported this cost was free: a reported 0 on a free model. A 0 on
    any other model is the route having no price, and no cost at all is nobody saying."""
    return (isinstance(reported_cost, (int, float)) and not isinstance(reported_cost, bool)
            and reported_cost == 0 and free_model(model, sources))


class Rates:
    """Rates per million tokens by model name, from one source."""

    def __init__(self, models: dict[str, dict], label: str, as_of: str | None = None):
        self.label, self.as_of = label, as_of
        self.models = {str(name).lower(): rates for name, rates in models.items() if isinstance(rates, dict)}
        tails: dict[str, list[str]] = {}
        for name in self.models:
            tails.setdefault(last_part(name), []).append(name)
        self._by_tail = {tail: names[0] for tail, names in tails.items() if len(names) == 1}

    def find(self, model: str | None) -> dict | None:
        name = str(model or "").strip().lower()
        if not name:
            return None
        for candidate in (name, name.removeprefix("openrouter/")):
            if candidate in self.models:
                return self.models[candidate]
        match = self._by_tail.get(last_part(name))      # a last part that fits two models fits neither
        return self.models[match] if match else None


def shipped(library_dir: Path) -> Rates:
    """The list that ships with the studio; an unreadable one is an empty list, never a guess."""
    try:
        data = json.loads((Path(library_dir) / "model-prices.json").read_text("utf-8"))
        models = {name: {**{kind: _rate(rates.get(kind)) for kind in (*KINDS, LONG_WRITE)},
                         "above": rates.get("above") if isinstance(rates.get("above"), dict) else None,
                         "free": _zero(rates.get("input")) and _zero(rates.get("output"))}
                  for name, rates in data["models"].items() if isinstance(rates, dict)}
        return Rates(models, "list", str(data.get("as_of") or "") or None)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return Rates({}, "list")


def from_gateway(model_info: object) -> Rates:
    """`GET /model/info` of a LiteLLM gateway → its own prices (per token there). An entry whose
    input and output prices are not both above zero is not a price (a plan route says 0 and 0)."""
    models = {}
    rows = model_info.get("data") if isinstance(model_info, dict) else None
    for row in rows if isinstance(rows, list) else []:
        info = row.get("model_info") if isinstance(row, dict) else None
        if not isinstance(info, dict) or not row.get("model_name"):
            continue
        per_token = {"input": info.get("input_cost_per_token"), "output": info.get("output_cost_per_token"),
                     "cache_read": info.get("cache_read_input_token_cost"),
                     "cache_write": info.get("cache_creation_input_token_cost"),
                     LONG_WRITE: info.get("cache_creation_input_token_cost_above_1hr")}
        rates = {kind: (_rate(value) and round(float(value) * MILLION, 6)) or None for kind, value in per_token.items()}
        if rates["input"] is None or rates["output"] is None:
            continue
        models[str(row["model_name"])] = {**rates, "above": _gateway_tier(info)}
    return Rates(models, "gateway")


# LiteLLM's fields for a higher rate above a prompt size (`…_above_200k_tokens`; 128k, 256k, 272k and
# 512k exist too). `_priority`/`_flex` endings are service tiers, not sizes: the `$` keeps them out.
TIER_FIELD = re.compile(r"^(input_cost_per_token|output_cost_per_token|cache_read_input_token_cost|"
                        r"cache_creation_input_token_cost|cache_creation_input_token_cost_above_1hr)_above_(\d+)k_tokens$")
TIER_KINDS = {"input_cost_per_token": "input", "output_cost_per_token": "output",
              "cache_read_input_token_cost": "cache_read", "cache_creation_input_token_cost": "cache_write",
              "cache_creation_input_token_cost_above_1hr": LONG_WRITE}


def _gateway_tier(info: dict) -> dict | None:
    """A gateway's long-prompt tier in the shipped list's shape, per million; None when it has none.
    A kind the tier gives no rate for stays None, so a request above the size that used it is unpriced."""
    found: dict[int, dict] = {}
    for field, value in info.items():
        match = TIER_FIELD.match(str(field))
        if match and _rate(value):
            found.setdefault(int(match.group(2)) * 1000, {})[TIER_KINDS[match.group(1)]] = round(float(value) * MILLION, 6)
    if not found:
        return None
    size = min(found)
    if len(found) > 1:      # LiteLLM's list has one size per model; two would need rules nobody wrote: above the first, unpriced
        return {"prompt_tokens": size, **{kind: None for kind in (*KINDS, LONG_WRITE)}}
    return {"prompt_tokens": size, **{kind: found[size].get(kind) for kind in (*KINDS, LONG_WRITE)}}


def _count(value) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def row_tokens(row: dict) -> dict | None:
    """A spend-log row → {input, cache_read, cache_write, output, prompt}: `prompt_tokens` includes the
    cached ones, so `input` is what is left. None when the row has no token counts to price."""
    prompt, completion = row.get("prompt_tokens"), row.get("completion_tokens")
    if not isinstance(prompt, (int, float)) or not isinstance(completion, (int, float)) or prompt + completion <= 0:
        return None
    usage = (row.get("metadata") or {}).get("usage_object") if isinstance(row.get("metadata"), dict) else None
    details = (usage or {}).get("prompt_tokens_details") if isinstance(usage, dict) else None
    details = details if isinstance(details, dict) else {}
    read = _count(details.get("cached_tokens"))
    write = _count(details.get("cache_write_tokens")) or _count(details.get("cache_creation_tokens"))
    if not details and isinstance(usage, dict):       # the Anthropic shape, where LiteLLM kept it
        read, write = _count(usage.get("cache_read_input_tokens")), _count(usage.get("cache_creation_input_tokens"))
    return {"input": max(0, int(prompt) - read - write), "cache_read": read, "cache_write": write,
            LONG_WRITE: long_write(usage), "output": _count(completion), "prompt": int(prompt)}


def long_write(usage: object) -> int | None:
    """How many of the cache-write tokens had the 1-hour lifetime, where the usage says so
    (Anthropic's `cache_creation`, LiteLLM's `cache_creation_token_details`); None when it does not."""
    if not isinstance(usage, dict):
        return None
    for key in ("cache_creation", "cache_creation_token_details"):
        split = usage.get(key)
        if isinstance(split, dict) and isinstance(split.get("ephemeral_1h_input_tokens"), int):
            return max(0, split["ephemeral_1h_input_tokens"])
    return None


def cost(tokens: dict, rates: dict | None, summed: bool = False) -> float | None:
    """Tokens times rates, in dollars; None unless there is a rate for every kind that was used.
    `summed`: the counts are a whole turn's, added up over its requests, so the size of each prompt
    is not known. Above a long-prompt tier that is unpriced (some requests may have been above it, or
    none); at or below it no request can have been above, so the ordinary rate holds."""
    if not rates:
        return None
    above = rates.get("above") or {}
    if above and isinstance(above.get("prompt_tokens"), (int, float)) and \
            tokens.get("prompt", tokens["input"] + tokens["cache_read"] + tokens["cache_write"]) > above["prompt_tokens"]:
        if summed:
            return None
        rates = {kind: _rate(above.get(kind)) for kind in (*KINDS, LONG_WRITE)}
    total = 0.0
    for kind in ("input", "output", "cache_read"):
        used = tokens.get(kind) or 0
        if not used:
            continue
        if rates.get(kind) is None:
            return None
        total += used * rates[kind] / MILLION
    written = tokens.get("cache_write") or 0
    if written:
        short, long = rates.get("cache_write"), rates.get(LONG_WRITE)
        if long is None or long == short:           # one rate whatever the lifetime
            if short is None:
                return None
            total += written * short / MILLION
        else:
            hour = tokens.get(LONG_WRITE)
            if not isinstance(hour, int):           # the lifetime changes the price and nobody said which
                return None
            hour = min(hour, written)
            if written - hour and short is None:
                return None
            total += ((written - hour) * (short or 0) + hour * long) / MILLION
    return total


class Pricer:
    """Prices a request from the first source that is complete for it, and remembers what it did."""

    def __init__(self, *sources: Rates):
        self.sources = [source for source in sources if source and source.models]
        self.requests, self.usd, self.used = 0, 0.0, set()

    def price(self, model: str | None, tokens: dict | None, summed: bool = False) -> float | None:
        if not tokens:
            return None
        found = [(source, source.find(model)) for source in self.sources]
        # The size above which a model costs more, from whichever source knows it. A source without a
        # tier gets that size with no rates, so above it that source is not complete for the request.
        sizes = [rates["above"]["prompt_tokens"] for _, rates in found
                 if rates and isinstance(rates.get("above"), dict)
                 and isinstance(rates["above"].get("prompt_tokens"), (int, float))]
        for source, rates in found:
            if rates and not rates.get("above") and sizes:
                rates = {**rates, "above": {"prompt_tokens": min(sizes)}}
            worked = cost(tokens, rates, summed)
            if worked is not None:
                self.requests += 1
                self.usd += worked
                self.used.add(source)
                return worked
        return None

    def row(self, row: dict) -> float | None:
        return self.price(row.get("model") or row.get("model_group"), row_tokens(row))

    def summary(self) -> dict | None:
        """→ the `backup` field, or None when the studio priced nothing."""
        if not self.requests:
            return None
        labels = {source.label for source in self.used}
        as_of = next((source.as_of for source in self.used if source.label == "list"), None)
        return {"requests": self.requests, "usd": round(self.usd, 6),
                "rates": "both" if len(labels) > 1 else next(iter(labels)), "as_of": as_of}
