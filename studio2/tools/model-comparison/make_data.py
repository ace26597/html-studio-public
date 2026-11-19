"""Sample data for the model comparison: one year of quotes for a made-up rooftop-solar installer.

Generated with a fixed seed, so every model gets the same file and the answers can be checked.
Patterns are built in on purpose (a spring peak, one city far ahead, one channel that closes best)
so that "what stands out" has something true to find.
"""
import csv, datetime, json, random

random.seed(20261119)
CITIES = {"Austin": 1.35, "Denver": 1.0, "Phoenix": 1.15, "Portland": 0.7, "Raleigh": 0.85, "Tampa": 0.95}
CHANNELS = {"Referral": (0.22, 0.62), "Website": (0.30, 0.38), "Partner": (0.16, 0.47), "Ads": (0.22, 0.24), "Walk-in": (0.10, 0.41)}
PEOPLE = {"Maya Okafor": 1.18, "Daniel Reyes": 1.0, "Priya Nair": 1.08, "Tom Whitfield": 0.82, "Lena Fischer": 0.97, "Chris Abara": 0.9}
SEASON = [0.7, 0.8, 1.15, 1.4, 1.5, 1.25, 1.0, 0.95, 1.05, 0.9, 0.7, 0.55]
BRANDS = ["Helios", "Suncrest", "Voltara"]

rows, n = [], 0
for month in range(1, 13):
    for city, weight in CITIES.items():
        count = round(6.2 * weight * SEASON[month - 1] + random.uniform(-1.2, 1.2))
        for _ in range(max(count, 1)):
            n += 1
            channel = random.choices(list(CHANNELS), [share for share, _ in CHANNELS.values()])[0]
            person = random.choice(list(PEOPLE))
            kw = round(random.choice([4.4, 5.2, 6.0, 6.6, 7.2, 8.0, 8.8, 9.6, 10.4, 12.0]) + random.uniform(-0.3, 0.3), 1)
            price = round(kw * random.uniform(2550, 3050) / 10) * 10
            day = random.randint(1, 28)
            closes = CHANNELS[channel][1] * PEOPLE[person]
            roll = random.random()
            if month >= 11 and roll > 0.72:
                stage = "Open"
            else:
                stage = "Won" if random.random() < closes else "Lost"
            days = "" if stage == "Open" else random.randint(6, 58)
            rows.append({"order_id": f"BL-{2500 + n}", "quote_date": datetime.date(2025, month, day).isoformat(), "city": city,
                         "channel": channel, "salesperson": person, "system_kw": kw, "price_usd": price, "stage": stage,
                         "days_to_close": days, "panel_brand": random.choice(BRANDS)})
random.shuffle(rows)
rows.sort(key=lambda r: r["quote_date"])
with open("orders.csv", "w", newline="") as out:
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)

def facts(subset):
    won = [r for r in subset if r["stage"] == "Won"]; lost = [r for r in subset if r["stage"] == "Lost"]
    return {"rows": len(subset), "won": len(won), "lost": len(lost), "open": len(subset) - len(won) - len(lost),
            "revenue": sum(r["price_usd"] for r in won),
            "win_rate": round(100 * len(won) / (len(won) + len(lost)), 2) if won or lost else None,
            "avg_kw": round(sum(r["system_kw"] for r in won) / len(won), 2) if won else None}

truth = {"all": facts(rows),
         "city": {c: facts([r for r in rows if r["city"] == c]) for c in CITIES},
         "channel": {c: facts([r for r in rows if r["channel"] == c]) for c in CHANNELS},
         "person": {p: facts([r for r in rows if r["salesperson"] == p]) for p in PEOPLE},
         "month": {m: facts([r for r in rows if int(r["quote_date"][5:7]) == m]) for m in range(1, 13)},
         "top_orders": [r["order_id"] for r in sorted((r for r in rows if r["stage"] == "Won"), key=lambda r: -r["price_usd"])[:15]]}
json.dump(truth, open("truth.json", "w"), indent=1)
a = truth["all"]
print(a)
print("best city by revenue:", max(truth["city"], key=lambda c: truth["city"][c]["revenue"]),
      "| best channel by win rate:", max(truth["channel"], key=lambda c: truth["channel"][c]["win_rate"]),
      "| best month:", max(truth["month"], key=lambda m: truth["month"][m]["revenue"]))
for c in CHANNELS: print("  ", c, truth["channel"][c]["win_rate"], truth["channel"][c]["revenue"])
for c in CITIES: print("  ", c, truth["city"][c]["revenue"], truth["city"][c]["win_rate"])
