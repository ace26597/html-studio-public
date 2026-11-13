"""MTA subway ridership (data.ny.gov), live Citi Bike GBFS snapshot, borough boundaries,
and reachability checks for sources we did not process. Writes data/profiles_other.json, data/access_checks.json."""
import json, time, urllib.request, urllib.parse, re

def get(u, timeout=180, raw=False):
    last = None
    for t in range(3):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "open-data-atlas"}), timeout=timeout)
            return r.read() if raw else json.load(r)
        except Exception as e: last = e; time.sleep(4)
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:120]}")

def soda(base, ds, p, timeout=240): return get(f"{base}/resource/{ds}.json?" + urllib.parse.urlencode(p), timeout)

out = []; checks = []
NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ---------- Borough boundaries (for Citi Bike point-in-polygon) ----------
bb = get("https://data.cityofnewyork.us/resource/gthc-hcne.geojson")
polys = {}
for f in bb["features"]:
    name = f["properties"].get("boroname") or f["properties"].get("boro_name")
    g = f["geometry"]; mp = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    polys[name] = mp
def inside(pt, ring):
    x, y = pt; c = False; n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][:2]; x2, y2 = ring[i - 1][:2]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: c = not c
    return c
def boro_of(lon, lat):
    for name, mp in polys.items():
        for poly in mp:
            if inside((lon, lat), poly[0]) and not any(inside((lon, lat), h) for h in poly[1:]): return name
    return None
checks.append(dict(name="Borough Boundaries (gthc-hcne)", url="https://data.cityofnewyork.us/resource/gthc-hcne.geojson", status="ok", label="Reachable · used",
                   note=f"Downloaded {len(polys)} borough shapes; used to place Citi Bike stations in boroughs."))

# ---------- Citi Bike live ----------
try:
    G = "https://gbfs.lyft.com/gbfs/2.3/bkn/en/"
    info = get(G + "station_information.json"); stat = get(G + "station_status.json")
    S = {s["station_id"]: s for s in info["data"]["stations"]}
    rows = []; agg = {}
    for st in stat["data"]["stations"]:
        s = S.get(st["station_id"]);
        if not s: continue
        b = boro_of(s["lon"], s["lat"]) or "Outside NYC borough shapes (mostly Jersey City & Hoboken)"
        ebikes = st.get("num_ebikes_available", 0); bikes = st.get("num_bikes_available", 0)
        a = agg.setdefault(b, dict(stations=0, bikes=0, ebikes=0, docks=0, empty=0))
        a["stations"] += 1; a["bikes"] += bikes; a["ebikes"] += ebikes; a["docks"] += st.get("num_docks_available", 0); a["empty"] += bikes == 0
        if b == "Manhattan": rows.append(dict(name=s["name"], bikes=bikes, ebikes=ebikes, docks=st.get("num_docks_available", 0), capacity=s.get("capacity"), renting="yes" if st.get("is_renting") else "no"))
    mn = agg.get("Manhattan", {}); snap = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(stat["last_updated"]))
    tot = sum(a["stations"] for a in agg.values())
    out.append(dict(id="citibike-gbfs", name="Citi Bike live station status", publisher="Lyft / Citi Bike (GBFS feed)", agency="Lyft, operator of Citi Bike", topic="Transport", category="Transportation",
        desc="General Bikeshare Feed Specification (GBFS) feed published by Citi Bike: every dock station with its location and capacity, plus live counts of bikes, e-bikes and free docks, refreshed about every minute. This is a single snapshot taken when the page data was built; stations were assigned to boroughs using NYC's official Borough Boundaries.",
        updated=stat["last_updated"], freq="about every 60 seconds", rows=tot, columns=[dict(field=k, name=k, type="", desc="") for k in ["station_id", "name", "lat", "lon", "capacity", "num_bikes_available", "num_ebikes_available", "num_docks_available", "is_renting"]],
        manhattan=dict(value="Manhattan", count=mn.get("stations")), page="https://citibikenyc.com/system-data", api=G + "station_status.json",
        byBorough=sorted([[k, v["stations"]] for k, v in agg.items()], key=lambda x: -x[1]),
        extra=[dict(v=f"{mn.get('bikes', 0):,}", l=f"bikes available at Manhattan docks at {snap}"), dict(v=f"{mn.get('ebikes', 0):,}", l="of which e-bikes"), dict(v=f"{mn.get('docks', 0):,}", l="free docks in Manhattan"), dict(v=f"{mn.get('empty', 0):,}", l="Manhattan stations with no bike at that moment")],
        top=[dict(title=f"Bikes available by borough, {snap}", rows=sorted([[k, v["bikes"]] for k, v in agg.items()], key=lambda x: -x[1])),
             dict(title="Manhattan stations with most bikes right then", rows=[[r["name"], r["bikes"]] for r in sorted(rows, key=lambda r: -r["bikes"])[:12]])],
        sample=sorted(rows, key=lambda r: r["name"])[:400], sampleCols=["name", "bikes", "ebikes", "docks", "capacity", "renting"], sampleNote="all Manhattan stations in the snapshot",
        caveat=f"Snapshot of one moment ({snap}), not an average. Historical trip files exist (see “What we could reach”) but were not processed.",
        queries=[G + "station_information.json", G + "station_status.json", "Borough Boundaries gthc-hcne (point-in-polygon)"]))
    checks.append(dict(name="Citi Bike GBFS live feed", url=G, status="ok", label="Reachable · used", note=f"{tot:,} stations in snapshot at {snap}."))
except Exception as e:
    checks.append(dict(name="Citi Bike GBFS live feed", url="https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json", status="fail", label="Failed", note=str(e)))

# ---------- MTA subway ridership ----------
NY = "https://data.ny.gov"; MID = "5wq4-mkjj"
try:
    v = get(f"{NY}/api/views/{MID}.json")
    cols = [dict(field=c["fieldName"], name=c["name"], type=c["dataTypeName"], desc=(c.get("description") or "")[:200]) for c in v["columns"] if not c["fieldName"].startswith(":@")]
    P = dict(id=MID, name=v["name"], publisher="data.ny.gov (New York State)", agency=v.get("attribution") or "Metropolitan Transportation Authority", topic="Transport", category=v.get("category"),
             desc=v.get("description", ""), updated=v.get("rowsUpdatedAt"), license=(v.get("license") or {}).get("name"), columns=cols,
             page=f"{NY}/d/{MID}", api=f"{NY}/resource/{MID}.json", queries=[], top=[], extra=[], monthlyUnit="entries per month (millions)", monthlyTitle="Subway entries per month")
    errs = []
    def r(p, label):
        P["queries"].append("; ".join(f"{k}={x}" for k, x in p.items()))
        return soda(NY, MID, p)
    try: P["rows"] = int(r({"$select": "count(*) as n"}, "rows")[0]["n"])
    except Exception as e: errs.append(f"row count: {e}")
    rng = "transit_timestamp >= '2025-01-01' and transit_timestamp < '2026-11-01'"
    try:
        t = r({"$select": "date_trunc_ym(transit_timestamp) as p, sum(ridership) as n", "$where": rng, "$group": "p", "$order": "p"}, "m")
        m = {x["p"]: float(x["n"]) for x in r({"$select": "date_trunc_ym(transit_timestamp) as p, sum(ridership) as n", "$where": rng + " and borough='Manhattan'", "$group": "p", "$order": "p"}, "mm")}
        P["monthly"] = [[x["p"][:7], round(float(x["n"])), round(m.get(x["p"], 0))] for x in t]
        P["monthlyUnit"] = "subway entries per month"
    except Exception as e: errs.append(f"trend: {e}")
    last = P.get("monthly", [[None]])[-1][0]
    if last:
        a = last + "-01T00:00:00"; y, mo = map(int, last.split("-")); b = f"{y + (mo == 12)}-{(mo % 12) + 1:02d}-01T00:00:00"
        w = f"transit_timestamp >= '{a}' and transit_timestamp < '{b}'"
        try:
            bb_ = r({"$select": "borough as k, sum(ridership) as n", "$where": w, "$group": "borough", "$order": "n desc"}, "b")
            P["byBorough"] = [[x["k"], round(float(x["n"]))] for x in bb_]; mnn = [x for x in P["byBorough"] if x[0] == "Manhattan"]
            P["manhattan"] = dict(value="Manhattan", count=None, why=f"Manhattan had {mnn[0][1]:,} subway entries in {last}" if mnn else "")
            P["extra"].append(dict(v=f"{mnn[0][1]:,}", l=f"subway entries at Manhattan stations, {last}"))
            P["extra"].append(dict(v=f"{100*mnn[0][1]/sum(x[1] for x in P['byBorough']):.1f}%", l=f"Manhattan share of all subway entries, {last}"))
            st = r({"$select": "station_complex as k, sum(ridership) as n", "$where": w + " and borough='Manhattan'", "$group": "station_complex", "$order": "n desc", "$limit": 12}, "s")
            P["top"].append(dict(title=f"Busiest Manhattan stations, {last} (entries)", rows=[[x["k"], round(float(x["n"]))] for x in st]))
            pm = r({"$select": "fare_class_category as k, sum(ridership) as n", "$where": w + " and borough='Manhattan'", "$group": "fare_class_category", "$order": "n desc"}, "f")
            P["top"].append(dict(title=f"Fare class at Manhattan stations, {last}", rows=[[x["k"], round(float(x["n"]))] for x in pm]))
            P["byBoroughTitle"] = f"Subway entries by borough, {last}"
        except Exception as e: errs.append(f"breakdown: {e}")
    try:
        P["sample"] = [{k: str(x.get(k, ""))[:60] for k in ["transit_timestamp", "station_complex", "payment_method", "fare_class_category", "ridership"]} for x in r({"$select": "transit_timestamp,station_complex,payment_method,fare_class_category,ridership", "$where": "borough='Manhattan'", "$order": "transit_timestamp desc", "$limit": 25}, "sample")]
    except Exception as e: errs.append(f"sample: {e}")
    P["caveat"] = "Ridership counts entries (taps and swipes) per station per hour, not unique riders. The by-borough chart for this dataset counts entries, not rows. Earlier years are in separate datasets (2020–2024: wujg-7c2s; 2017–2019: t69i-h2me)."
    if errs: P["error"] = "; ".join(errs)
    out.append(P)
    checks.append(dict(name="MTA Subway Hourly Ridership (data.ny.gov)", url=f"{NY}/resource/{MID}.json", status="ok" if not errs else "partial", label="Reachable · used" if not errs else "Partly fetched", note="Aggregated by month, borough and station." + (" Problems: " + P.get("error", "") if errs else "")))
except Exception as e:
    checks.append(dict(name="MTA Subway Hourly Ridership (data.ny.gov)", url=f"{NY}/resource/{MID}.json", status="fail", label="Failed", note=str(e)))

# ---------- reachability of sources we did not process ----------
def head(u):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, method="HEAD", headers={"User-Agent": "open-data-atlas"}), timeout=30)
        return r.status, int(r.headers.get("Content-Length") or 0)
    except Exception as e: return str(e)[:80], 0
try:
    t = get("https://s3.amazonaws.com/tripdata", raw=True).decode()
    keys = [(k, int(s)) for k, s in re.findall(r"<Key>(.*?)</Key>.*?<Size>(\d+)</Size>", t, re.S) if re.match(r"^\d{6}-citibike", k)]
    k, s = keys[-1]
    checks.append(dict(name="Citi Bike historical trip files", url="https://s3.amazonaws.com/tripdata/", status="partial", label="Reachable · not processed",
                       note=f"{len(keys)} monthly NYC archives listed; newest is {k} ({s/1e6:,.0f} MB). Each full month is around 1 GB zipped, too large to process for this page."))
except Exception as e:
    checks.append(dict(name="Citi Bike historical trip files", url="https://s3.amazonaws.com/tripdata/", status="fail", label="Failed", note=str(e)))
for ym in ["2026-09", "2026-08"]:
    st, n = head(f"https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{ym}.parquet")
    if st == 200:
        checks.append(dict(name="TLC trip record files (yellow, green, for-hire, high-volume FHV)", url="https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page", status="partial", label="Reachable · not processed",
                           note=f"Newest yellow-taxi month found: {ym} ({n/1e6:,.0f} MB Parquet). Not processed; only the 2023 yellow-taxi table on NYC Open Data is profiled."))
        break
st, _ = head("https://api.mta.info/")
checks.append(dict(name="MTA real-time GTFS feeds (subway arrivals, bus positions)", url="https://api.mta.info/", status="partial" if st == 200 else "fail", label="Reachable · not processed" if st == 200 else "Failed",
                   note="Portal answers. Feeds are binary protocol buffers that change every few seconds; this static page does not show live arrivals." if st == 200 else str(st)))

json.dump(dict(fetched=NOW, datasets=out), open("data/profiles_other.json", "w"))
json.dump(dict(tested=NOW, checks=checks), open("data/access_checks.json", "w"))
print("done", [d["id"] for d in out], [c["label"] for c in checks])
