"""Five-year cost model. Every input is a sourced figure (see data/inputs.json 'src').
Writes data/model.json with AAA 5-year totals, the worked example and the miles sensitivity."""
import json, pathlib

root = pathlib.Path(__file__).resolve().parent.parent
inp = json.loads((root / "data/inputs.json").read_text())

# AAA 2026 side-by-side (s51), annual at 15k mi/yr -> five-year totals
aaa = inp["aaa2026"]
aaa5 = {}
for cat, d in aaa.items():
    aaa5[cat] = {}
    for pt in ("ev", "gas", "hybrid"):
        comp = {k: v * 5 for k, v in d[pt].items()}
        comp["total"] = sum(comp.values())
        aaa5[cat][pt] = comp
    aaa5[cat]["ev_minus_gas"] = aaa5[cat]["ev"]["total"] - aaa5[cat]["gas"]["total"]


def five_year(p, miles=None, ev_dep=None, public_share=None, incentive=0):
    m = miles if miles is not None else p["miles"]
    years = 5
    mi = m * years
    evdep = ev_dep if ev_dep is not None else p["ev_dep_pct"]
    pub = public_share if public_share is not None else p["public_share"]
    kwh = mi * p["ev_kwh_per_100mi"] / 100
    ev_energy = kwh * ((1 - pub) * p["home_price"] + pub * p["dcfc_price"])
    gas_energy = mi / p["gas_mpg"] * p["gas_price"]
    ev = {
        # depreciation measured on the sticker price; a point-of-sale incentive lowers net cost one-for-one
        "depreciation": p["ev_price"] * evdep / 100 - incentive,
        "energy": ev_energy,
        "maintenance": mi * p["ev_maint_per_mi"],
        "insurance": p["ev_ins"] * years,
        "ev_fee": p["ev_fee"] * years,
    }
    gas = {
        "depreciation": p["gas_price_car"] * p["gas_dep_pct"] / 100,
        "energy": gas_energy,
        "maintenance": mi * p["gas_maint_per_mi"],
        "insurance": p["gas_ins"] * years,
        "ev_fee": 0,
    }
    ev["total"] = sum(ev.values()); gas["total"] = sum(gas.values())
    return {"ev": ev, "gas": gas, "diff": ev["total"] - gas["total"], "kwh": kwh, "gallons": mi / p["gas_mpg"]}


p = inp["example"]
base = five_year(p)
sens = []
for m in range(5000, 25001, 2500):
    sens.append({
        "miles": m,
        "base": round(five_year(p, miles=m)["diff"]),
        "home_only": round(five_year(p, miles=m, public_share=0)["diff"]),
        "same_dep": round(five_year(p, miles=m, ev_dep=p["gas_dep_pct"])["diff"]),
    })
# break-even miles for base (linear in miles)
d0 = five_year(p, miles=0)["diff"]; d1 = five_year(p, miles=10000)["diff"]
slope = (d1 - d0) / 10000
breakeven = -d0 / slope if slope < 0 else None

out = {"aaa5": aaa5, "example": base, "sensitivity": sens, "breakeven_miles_per_year": breakeven,
       "per_mile": {"ev_energy": base["ev"]["energy"] / (p["miles"] * 5), "gas_energy": base["gas"]["energy"] / (p["miles"] * 5)}}
(root / "data/model.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
