import json, urllib.request
S = json.load(open("data/profiles_socrata.json")); O = json.load(open("data/profiles_other.json")); A = json.load(open("data/access_checks.json"))
# DOF violation code definitions for parking tickets
try:
    codes = {x["code"]: x["definition"] for x in json.load(urllib.request.urlopen("https://data.cityofnewyork.us/resource/ncbg-6agr.json?$limit=500", timeout=60))}
    json.dump(codes, open("data/parking_violation_codes.json", "w"))
except Exception: codes = json.load(open("data/parking_violation_codes.json"))
for p in S["datasets"]:
    if p["id"] == "pvqr-7yc4":
        for t in p["top"]:
            if t["title"].startswith("Top violation codes"):
                t["rows"] = [[f"{k} · {codes.get(str(k), '?').title()}", n] for k, n in t["rows"]]; t["note"] = "Code definitions from DOF Parking Violation Codes (ncbg-6agr)"
for p in S["datasets"]:
    if p["id"] == "hn5i-inap":
        for t in p["top"]:
            if "species" in t["title"]:
                t["rows"] = [[(k.split(" - ", 1)[1].capitalize() if k and " - " in k else k), n] for k, n in t["rows"]]; t["note"] = "Common names from the genusspecies field"
ds = S["datasets"] + O["datasets"]
for p in ds:
    p.setdefault("publisher", "NYC Open Data")
    if p["id"] == "5wq4-mkjj": p["manhattan"] = dict(value="Manhattan", count=None, why="rows are station-hours; Manhattan entry totals are shown below")
json.dump(dict(fetched=S["fetched"], datasets=ds), open("data/profiles.json", "w"))
checks = [dict(name="NYC Open Data catalog API", url="https://api.us.socrata.com/api/catalog/v1?domains=data.cityofnewyork.us", status="ok", label="Reachable · used", note="All 3,026 catalog entries downloaded (the first request returned HTTP 503; a retry succeeded).")]
for p in S["datasets"]:
    checks.append(dict(name=f"{p['name']} ({p['id']})", url=p["api"], status="partial" if p.get("error") else "ok", label="Partly fetched" if p.get("error") else "Reachable · used", note=p.get("error") or f"{p['rows']:,} rows; aggregates computed by the API."))
checks += A["checks"]
unreach = [
  dict(what="Live on-street parking availability", why="No dataset in the NYC Open Data catalog reports free curb spaces or garage occupancy. The city publishes meter locations, rate zones and tickets (all profiled or listed here), but not real-time availability."),
  dict(what="Paid-parking transactions (ParkNYC / meter payments)", why="Not found in the catalog; only meter locations and block faces are published."),
  dict(what="Citi Bike and TLC trip-level history", why="Reachable as monthly files (about 1 GB per Citi Bike month, 60 MB per yellow-taxi month), but too large to process for a static page. Only the 2023 yellow-taxi table on NYC Open Data is profiled."),
  dict(what="Real-time subway and bus positions", why="MTA GTFS-realtime feeds are reachable but change every few seconds; a static dashboard cannot show them. A small back end could add them later."),
  dict(what="Street Tree data after 2016", why="The 2015 census is the latest full tree census on NYC Open Data. Parks' newer tree inventory (Forestry Tree Points) is in the catalog but not profiled here."),
]
json.dump(dict(tested=A["tested"], checks=checks, unreachable=unreach), open("data/access.json", "w"))
print(len(ds), "profiles;", len(checks), "checks")
