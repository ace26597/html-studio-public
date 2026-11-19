#!/usr/bin/env python3
"""Write `library/model-prices.json`, the price list the studio's backup cost calculation uses
(CONTRACT.md, "The backup calculation"). No key is needed and nothing about this machine is sent:

    .venv/bin/python tools/prices/update.py            # fetch OpenRouter's public model list
    .venv/bin/python tools/prices/update.py --from saved.json

Every price is copied from the fetched list (US dollars per token there, per million here); none is
typed in this file, with one exception OpenRouter's list does not hold: a model's higher rate for
long prompts (TIERS), copied from the provider's own pricing page, whose address is kept beside it.
Batch, free and other `:variant` entries are left out, as is any model without both an input and an
output price above zero.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://openrouter.ai/api/v1/models"
TARGET = Path(__file__).resolve().parents[2] / "library" / "model-prices.json"

# Anthropic bills a cache entry written with the 1-hour lifetime at twice the input rate (the
# 5-minute one, which is what the fetched list holds, at 1.25 times). The rule and the table that
# shows it per model: https://platform.claude.com/docs/en/about-claude/pricing ("1h cache writes").
# The agent program the studio runs writes 1-hour entries, so the difference is real money.
ONE_HOUR_WRITE = {"prefix": "anthropic/", "times_input": 2.0,
                  "source": "https://platform.claude.com/docs/en/about-claude/pricing"}

# A higher rate above a prompt size, per million tokens. Copied on 13 November 2026 from the table at
# the address given ("Claude Haiku 5.5 (for prompts over 100,000 tokens)").
TIERS = {
    "anthropic/claude-haiku-5.5": {
        "prompt_tokens": 100_000, "input": 0.50, "output": 2.50, "cache_read": 0.05, "cache_write": 0.625,
        "cache_write_1h": 1.0,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing"},
}


def per_million(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number * 1_000_000, 6) if number > 0 else None


def build(listing: dict, today: str) -> dict:
    models = {}
    for item in listing.get("data") or []:
        name, pricing = item.get("id"), item.get("pricing") or {}
        if not isinstance(name, str) or ":" in name or "/" not in name:
            continue
        rates = {"input": per_million(pricing.get("prompt")), "output": per_million(pricing.get("completion")),
                 "cache_read": per_million(pricing.get("input_cache_read")),
                 "cache_write": per_million(pricing.get("input_cache_write"))}
        if rates["input"] is None or rates["output"] is None:
            continue
        if name.startswith(ONE_HOUR_WRITE["prefix"]):
            rates["cache_write_1h"] = round(rates["input"] * ONE_HOUR_WRITE["times_input"], 6)
        if name in TIERS:
            rates["above"] = TIERS[name]
        takes = (item.get("architecture") or {}).get("input_modalities")
        if isinstance(takes, list) and takes and "image" not in takes:
            rates["images"] = False      # a text-only model (CONTRACT.md, "A model that cannot take pictures")
        models[name] = rates
    return {"as_of": today, "source": SOURCE, "one_hour_cache_write": ONE_HOUR_WRITE,
            "note": "US dollars per million tokens. Written by tools/prices/update.py; do not edit by hand.",
            "models": dict(sorted(models.items()))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="saved", type=Path, help="a saved copy of the list, instead of fetching it")
    args = parser.parse_args()
    if args.saved:
        listing = json.loads(args.saved.read_text("utf-8"))
    else:
        request = urllib.request.Request(SOURCE, headers={"User-Agent": "studio-price-list"})
        with urllib.request.urlopen(request, timeout=30) as response:   # noqa: S310 - a fixed https address
            listing = json.load(response)
    prices = build(listing, datetime.date.today().isoformat())
    if len(prices["models"]) < 50:
        print(f"Only {len(prices['models'])} priced models came back; the list was not written.", file=sys.stderr)
        return 1
    TARGET.write_text(json.dumps(prices, indent=1) + "\n", "utf-8")
    print(f"{len(prices['models'])} models, as of {prices['as_of']}, in {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
