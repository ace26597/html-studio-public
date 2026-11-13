"""Profile key NYC datasets through the Socrata SODA API. Writes data/profiles.json.
Each profile: Manhattan vs city counts, by-borough, monthly trend, top categories, sample rows."""
import json, time, urllib.request, urllib.parse, sys, concurrent.futures as cf

META = json.load(open("data/meta.json"))
NYC = "https://data.cityofnewyork.us"
M24 = ("2024-11-01", "2026-11-01")   # 24 complete months
Y1 = "created_date >= '2025-11-01' and created_date < '2026-11-01'"
LOG = []

def q(base, ds, params, timeout=150):
    u = f"{base}/resource/{ds}.json?" + urllib.parse.urlencode(params)
    last = None
    for t in range(3):
        try:
            return json.load(urllib.request.urlopen(u, timeout=timeout))
        except Exception as e:
            last = e; time.sleep(4 * (t + 1))
    raise RuntimeError(f"{type(last).__name__}: {str(last)[:120]}")

def AND(*w): return " and ".join(f"({x})" for x in w if x)

C = [  # configuration per dataset
 dict(id="uvpi-gqnh", topic="Environment", boro="boroname", mn="'Manhattan'",
      tops=[("spc_common", "Most common species in Manhattan"), ("health", "Tree health in Manhattan"), ("nta_name", "Manhattan neighbourhoods with most trees")],
      extra=[("avg(tree_dbh)", "average trunk diameter in Manhattan, inches", 1)],
      sample=["tree_id", "spc_common", "health", "tree_dbh", "address", "nta_name", "status"], order="tree_id",
      caveat="A one-time census carried out in 2015–2016; it has not been updated since. Trees planted or removed after 2016 are not reflected."),
 dict(id="hn5i-inap", topic="Environment", boro=":@computed_region_yeji_bk3q", mn="'4'", scope="tpstructure = 'Full'", scopeLabel="living trees only (structure = Full)",
      boroLabels={"1": "Staten Island", "2": "Brooklyn", "3": "Queens", "4": "Manhattan", "5": "Bronx"},
      tops=[("genusspecies", "Most common species, living Manhattan trees"), ("tpcondition", "Condition of living Manhattan trees"), ("riskrating", "Risk rating, living Manhattan trees")],
      extra=[("count(*)", "living street trees in Manhattan (structure = Full)", 0), ("avg(dbh)", "average trunk diameter, living Manhattan trees, inches", 1)],
      sample=["objectid", "genusspecies", "tpcondition", "dbh", "planteddate", "updateddate"], order="updateddate desc",
      caveat="Parks' live forestry inventory, updated continuously. It has no borough column: boroughs come from the Socrata-computed borough region attached to each point (code 4 = Manhattan, checked against Bicycle Parking, which has both fields). It also keeps stumps and retired trees; the charts and the living-tree count use structure = Full only, while the headline row count includes every record."),
 dict(id="erm2-nwe9", topic="311", boro="borough", mn="'MANHATTAN'", date="created_date", scope=Y1, scopeLabel="Nov 2025 – Oct 2026",
      tops=[("complaint_type", "Top complaint types in Manhattan (last 12 months)"), ("agency", "Agency handling Manhattan requests (last 12 months)"), ("incident_zip", "Manhattan ZIP codes with most requests (last 12 months)")],
      sample=["created_date", "complaint_type", "descriptor", "incident_address", "agency", "status"], order="created_date desc",
      caveat="Borough breakdown and top lists cover the last 12 complete months (Nov 2025 – Oct 2026) to keep queries fast on 23 million rows; the row count covers 2020 to today."),
 dict(id="pvqr-7yc4", topic="Parking", boro=None, mnwhere="violation_precinct between 1 and 34", mnLabel="Manhattan precincts (1–34)", date="issue_date",
      months=("2026-07-01", "2026-11-01"),
      tops=[("violation_code", "Top violation codes in Manhattan"), ("vehicle_make", "Most-ticketed vehicle makes in Manhattan"), ("registration_state", "Plate state of ticketed vehicles in Manhattan"), ("street_name", "Manhattan streets with most tickets")],
      sample=["issue_date", "violation_code", "street_name", "vehicle_make", "registration_state", "violation_precinct"], order="summons_number desc",
      caveat="Fiscal year 2027 started 1 July 2026, so this covers July–October 2026 so far. There is no borough column: Manhattan is taken as violation precincts 1–34 (NYPD's Manhattan precincts). A few rows carry impossible issue dates (e.g. 2066); the trend only uses July–October 2026."),
 dict(id="nc67-uf89", topic="Parking", boro=None, mnwhere="county in ('NY','MN')", mnLabel="county NY / MN", heavy=True,
      tops=[("violation", "Manhattan violations (issued 2026)", "issue_date like '%/2026'")],
      sample=["issue_date", "violation", "fine_amount", "county", "issuing_agency", "violation_status"], order="summons_number desc",
      caveat="153.7 million rows. The API cannot group the full table within its time limit, so the breakdowns here are limited to tickets whose issue date is in 2026; issue dates are stored as text (MM/DD/YYYY), so no monthly trend is drawn."),
 dict(id="693u-uax6", topic="Parking", boro="borough", mn="'Manhattan'",
      tops=[("status", "Meter status in Manhattan"), ("meter_hours", "Most common meter rules in Manhattan"), ("on_street", "Manhattan streets with most meters")],
      sample=["meter_number", "status", "meter_hours", "on_street", "from_street", "to_street"], order="meter_number"),
 dict(id="h9gi-nx95", topic="Transport", boro="borough", mn="'MANHATTAN'", date="crash_date", scope="crash_date >= '2025-11-01' and crash_date < '2026-11-01'", scopeLabel="Nov 2025 – Oct 2026",
      tops=[("contributing_factor_vehicle_1", "Main contributing factor, Manhattan crashes (last 12 months)"), ("on_street_name", "Manhattan streets with most crashes (last 12 months)")],
      extra=[("sum(number_of_persons_injured)", "people injured in Manhattan crashes, last 12 months", 0), ("sum(number_of_persons_killed)", "people killed in Manhattan crashes, last 12 months", 0)],
      sample=["crash_date", "crash_time", "on_street_name", "cross_street_name", "number_of_persons_injured", "contributing_factor_vehicle_1"], order="crash_date desc",
      caveat="Many crash records have no borough filled in, so borough totals understate the true count. Breakdowns cover Nov 2025 – Oct 2026."),
 dict(id="5uac-w243", topic="Public safety", boro="boro_nm", mn="'MANHATTAN'", date="rpt_dt", months=("2026-01-01", "2026-10-01"),
      tops=[("ofns_desc", "Top offence types in Manhattan"), ("law_cat_cd", "Severity (felony / misdemeanour / violation), Manhattan"), ("prem_typ_desc", "Premises type, Manhattan")],
      sample=["rpt_dt", "ofns_desc", "law_cat_cd", "prem_typ_desc", "addr_pct_cd"], order="rpt_dt desc",
      caveat="Year-to-date file: covers complaints reported since 1 January 2026, published quarterly with a lag. The trend counts by report date."),
 dict(id="uip8-fykc", topic="Public safety", boro="arrest_boro", mn="'M'", date="arrest_date", months=("2026-01-01", "2026-10-01"),
      tops=[("ofns_desc", "Top arrest offences in Manhattan"), ("age_group", "Age group of people arrested in Manhattan"), ("law_cat_cd", "Charge level, Manhattan")],
      sample=["arrest_date", "ofns_desc", "law_cat_cd", "arrest_precinct", "age_group", "perp_sex"], order="arrest_date desc",
      boroLabels={"M": "Manhattan", "B": "Bronx", "K": "Brooklyn", "Q": "Queens", "S": "Staten Island"},
      caveat="Year-to-date file covering arrests since 1 January 2026, published quarterly with a lag."),
 dict(id="5ucz-vwe8", topic="Public safety", boro="boro", mn="'MANHATTAN'", date="occur_date", yearly=True,
      tops=[("location_desc", "Location type of Manhattan shootings"), ("precinct", "Manhattan precincts with most shootings")],
      sample=["occur_date", "occur_time", "precinct", "location_desc", "loc_of_occur_desc"], order="occur_date desc",
      caveat="Counts shooting incidents from 2006. In this dataset the latitude and longitude fields appear swapped (latitude holds values near −73.9)."),
 dict(id="43nn-pn8j", topic="Health", boro="boro", mn="'Manhattan'", date="inspection_date", distinct="camis",
      tops=[("cuisine_description", "Cuisines in Manhattan (restaurants)", None, "camis"), ("grade", "Letter grades on Manhattan inspection rows"), ("violation_description", "Most cited violations in Manhattan")],
      sample=["inspection_date", "dba", "cuisine_description", "grade", "street", "violation_code"], order="inspection_date desc",
      caveat="Each row is one violation from one inspection, so row counts are not restaurant counts; the cuisine chart counts distinct restaurants (CAMIS id). Rows dated 1900-01-01 are restaurants not yet inspected and are excluded from the trend."),
 dict(id="p937-wjvj", topic="Health", boro="borough", mn="'Manhattan'", date="inspection_date", scope="inspection_date >= '2025-11-01' and inspection_date < '2026-11-01'", scopeLabel="Nov 2025 – Oct 2026",
      tops=[("result", "Inspection results in Manhattan (last 12 months)"), ("inspection_type", "Inspection type, Manhattan (last 12 months)"), ("zip_code", "Manhattan ZIP codes with most inspections (last 12 months)")],
      sample=["inspection_date", "inspection_type", "result", "house_number", "street_name", "zip_code"], order="inspection_date desc"),
 dict(id="vfnx-vebw", topic="Environment", boro=None, mnall=True,
      tops=[("primary_fur_color", "Primary fur colour"), ("age", "Age"), ("shift", "Shift (AM / PM)"), ("location", "Where it was seen")],
      sample=["unique_squirrel_id", "hectare", "shift", "date", "age", "primary_fur_color", "location"], order="unique_squirrel_id",
      caveat="All 3,023 sightings are in Central Park, Manhattan, over two weeks in October 2018."),
 dict(id="usep-8jbt", topic="Housing", boro="borough", mn="'1'", date="sale_date",
      boroLabels={"1": "Manhattan", "2": "Bronx", "3": "Brooklyn", "4": "Queens", "5": "Staten Island"},
      tops=[("neighborhood", "Manhattan neighbourhoods with most sales"), ("building_class_category", "Building class of Manhattan sales")],
      extra=[("median(sale_price)", "median Manhattan sale price (sales over $10,000)", 0, "sale_price > 10000"), ("count(*)", "Manhattan sales at $0 (transfers, not market sales)", 0, "sale_price = 0")],
      sample=["sale_date", "neighborhood", "address", "building_class_category", "sale_price", "total_units"], order="sale_date desc",
      caveat="A rolling 12-month file of property sales. Many rows have a $0 sale price (transfers between relatives, deeds without consideration); the median excludes sales of $10,000 or less."),
 dict(id="64uk-42ks", topic="Housing", boro="borough", mn="'MN'", builtDecades=True,
      boroLabels={"MN": "Manhattan", "BX": "Bronx", "BK": "Brooklyn", "QN": "Queens", "SI": "Staten Island"},
      tops=[("landuse", "Land use of Manhattan tax lots"), ("zonedist1", "Main zoning districts in Manhattan")],
      extra=[("sum(unitsres)", "residential units on Manhattan lots", 0), ("avg(numfloors)", "average floors per Manhattan lot", 1), ("median(yearbuilt)", "median year built (Manhattan, built lots)", "y", "yearbuilt > 0")],
      sample=["address", "bldgclass", "landuse", "yearbuilt", "numfloors", "unitsres", "zonedist1"], order="unitsres desc",
      landuse={"1": "1–2 family homes", "2": "Multi-family walk-up", "3": "Multi-family elevator", "4": "Mixed residential & commercial", "5": "Commercial & office", "6": "Industrial & manufacturing", "7": "Transportation & utility", "8": "Public facilities & institutions", "9": "Open space & outdoor recreation", "10": "Parking facilities", "11": "Vacant land"}),
 dict(id="yjub-udmw", topic="City services", boro="boroname", mn="'Manhattan'",
      tops=[("provider", "Hotspot providers in Manhattan"), ("type", "Free or limited, Manhattan"), ("ntaname", "Manhattan neighbourhoods with most hotspots")],
      sample=["name", "provider", "type", "location", "ntaname"], order="objectid",
      caveat="Last updated in 2019; LinkNYC kiosks and other hotspots added since are not included."),
 dict(id="tg4x-b46p", topic="Culture", boro="borough", mn="'Manhattan'", date="startdatetime",
      tops=[("category", "Production category, Manhattan"), ("eventtype", "Permit type, Manhattan"), ("subcategoryname", "Sub-category, Manhattan")],
      sample=["startdatetime", "category", "subcategoryname", "eventtype", "parkingheld"], order="startdatetime desc"),
 dict(id="nu7n-tubp", topic="Animals", boro=None, mnwhere="zipcode between 10001 and 10282", mnLabel="Manhattan ZIP codes 10001–10282",
      tops=[("breedname", "Most licensed breeds in Manhattan"), ("animalname", "Most common dog names in Manhattan"), ("extract_year", "Records per extract year (Manhattan)")],
      sample=["animalname", "breedname", "animalgender", "animalbirth", "zipcode", "licenseissueddate"], order="licenseissueddate desc",
      caveat="There is no borough column; Manhattan is taken as ZIP codes 10001–10282. The file stacks yearly extracts, so the same dog can appear in several years: counts are licence records, not dogs."),
 dict(id="rsgh-akpg", topic="Animals", boro="borough", mn="'Manhattan'", date="dateofbite", yearly=True,
      tops=[("breed", "Breeds named in Manhattan bite reports")],
      sample=["dateofbite", "species", "breed", "gender", "zipcode"], order="dateofbite desc"),
 dict(id="hxx3-bwgv", topic="Animals", boro="borough", mn="'M'", boroLabels={"M": "Manhattan", "X": "Bronx", "B": "Brooklyn", "Q": "Queens", "R": "Staten Island"},
      tops=[("dog_area_type", "Type of dog area, Manhattan")], sample=["name", "dog_area_type", "zipcode", "featurestatus"], order="name"),
 dict(id="6z8x-wfk4", topic="Housing", boro="borough", mn="'MANHATTAN'", date="executed_date", yearly=True,
      tops=[("residential_commercial_ind", "Residential or commercial, Manhattan"), ("nta", "Manhattan neighbourhoods with most evictions"), ("eviction_zip", "Manhattan ZIP codes with most evictions")],
      sample=["executed_date", "eviction_address", "residential_commercial_ind", "eviction_zip", "nta"], order="executed_date desc",
      caveat="Evictions carried out by city marshals since 2017. 2020–2021 were affected by the pandemic eviction moratorium."),
 dict(id="ipu4-2q9a", topic="Buildings", boro="borough", mn="'MANHATTAN'",
      tops=[("job_type", "Job type, Manhattan permits"), ("permit_type", "Permit type, Manhattan"), ("work_type", "Work type, Manhattan")],
      sample=["issuance_date", "job_type", "permit_type", "house__", "street_name", "permit_status"], order="dobrundate desc",
      caveat="Legacy DOB (BIS) permits. Dates are stored as text, so no trend is drawn. Newer filings are in “DOB NOW: Build – Approved Permits” (rbx6-tga4), listed in the catalog."),
 dict(id="ygpa-z7cr", topic="Housing", boro="borough", mn="'MANHATTAN'", date="received_date", scope="received_date >= '2025-11-01' and received_date < '2026-11-01'", scopeLabel="Nov 2025 – Oct 2026",
      tops=[("major_category", "Problem category, Manhattan (last 12 months)"), ("problem_code", "Specific problem, Manhattan (last 12 months)")],
      sample=["received_date", "major_category", "problem_code", "house_number", "street_name", "complaint_status"], order="received_date desc",
      caveat="Each row is one problem; a complaint can list several. Heat complaints peak in winter."),
 dict(id="wvxf-dwi5", topic="Housing", boro="boro", mn="'MANHATTAN'", date="inspectiondate", scope="inspectiondate >= '2025-11-01' and inspectiondate < '2026-11-01'", scopeLabel="Nov 2025 – Oct 2026",
      tops=[("class", "Violation class, Manhattan (A = non-hazardous, B = hazardous, C = immediately hazardous)"), ("currentstatus", "Current status, Manhattan")],
      sample=["inspectiondate", "class", "housenumber", "streetname", "currentstatus", "novdescription"], order="inspectiondate desc"),
 dict(id="592z-n7dk", topic="Transport", boro="boroname", mn="'Manhattan'",
      tops=[("racktype", "Rack type, Manhattan"), ("ntaname", "Manhattan neighbourhoods with most racks"), ("program", "Programme, Manhattan")],
      sample=["site_id", "ifoaddress", "onstreet", "racktype", "date_inst", "ntaname"], order="date_inst desc"),
 dict(id="4b4i-vvec", topic="Transport", boro=None, date="tpep_pickup_datetime", months=("2023-01-01", "2024-01-01"),
      noMn="pickup zones are numbered (pulocationid) — no borough column",
      tops=[("payment_type", "Payment type (1 card, 2 cash, 3 no charge, 4 dispute)"), ("passenger_count", "Passengers per trip")],
      extra=[("avg(fare_amount)", "average metered fare, $", 2), ("avg(trip_distance)", "average trip distance, miles", 2), ("avg(tip_amount)", "average tip, $", 2)],
      sample=["tpep_pickup_datetime", "pulocationid", "dolocationid", "trip_distance", "fare_amount", "tip_amount", "total_amount"], order="tpep_pickup_datetime",
      caveat="NYC Open Data hosts yellow-taxi trips only up to 2023. Newer months exist as Parquet files from TLC but were not processed here (see “What we could reach”). Averages include all rows, including some with negative or zero fares."),
 dict(id="uiay-nctu", topic="Transport", boro="boroughname", mn="'Manhattan'",
      tops=[("reviewstat", "Approval type, Manhattan"), ("orgname", "Organisers in Manhattan")],
      sample=["orgname", "appronstre", "apprfromst", "apprtostre", "apprdayswe", "apprstartd", "apprenddat"], order="apprstartd desc"),
]

def mnw(c):
    if c.get("mnall"): return None
    if c.get("mnwhere"): return c["mnwhere"]
    if c.get("boro"): return f"{c['boro']} = {c['mn']}"
    return "false"

def profile(c, base=NYC):
    i = c["id"]; m = META[i]; P = dict(id=i, name=m["name"], desc=m["desc"], agency=m["agency"], category=m["category"], topic=c["topic"],
        updated=m["updated"], freq=m.get("freq"), rows=m["rows"], license=m.get("license"), caveat=c.get("caveat"),
        columns=m["columns"], page=f"{base}/d/{i}", api=f"{base}/resource/{i}.json", queries=[], top=[], extra=[])
    errs = []
    scope = c.get("scope"); W = mnw(c); lab = c.get("boroLabels", {})
    def run(params, note):
        P["queries"].append("; ".join(f"{k}={v}" for k, v in params.items()))
        return q(base, i, params)
    # Manhattan count + borough breakdown
    try:
        if c.get("boro"):
            r = run({"$select": f"{c['boro']} as k, count(*) as n", "$where": scope or f"{c['boro']} is not null", "$group": c['boro'], "$order": "n desc", "$limit": 20}, "boro")
            P["byBorough"] = [[lab.get(x.get("k"), x.get("k")), int(x["n"])] for x in r]
            mnv = lab.get(c["mn"].strip("'"), c["mn"].strip("'"))
            hit = [x for x in P["byBorough"] if str(x[0]).upper() == str(mnv).upper()]
            if scope:
                P["manhattan"] = dict(value=mnv, count=int(run({"$select": "count(*) as n", "$where": W}, "mn")[0]["n"]))
            else:
                P["manhattan"] = dict(value=mnv, count=hit[0][1] if hit else 0)
            if scope: P["byBoroughNote"] = c.get("scopeLabel")
        elif c.get("mnall"):
            P["manhattan"] = dict(value="Manhattan", count=m["rows"])
        elif c.get("noMn"):
            P["manhattan"] = dict(why=c["noMn"])
        elif not c.get("heavy"):
            P["manhattan"] = dict(value="Manhattan", count=int(run({"$select": "count(*) as n", "$where": W}, "mn")[0]["n"]), label=c.get("mnLabel"))
        else:
            P["manhattan"] = dict(why=f"Manhattan ({c.get('mnLabel')}) count timed out")
            try: P["manhattan"] = dict(value="Manhattan", count=int(q(base, i, {"$select": "count(*) as n", "$where": W}, timeout=240)[0]["n"]), label=c.get("mnLabel"))
            except Exception as e: errs.append(f"Manhattan count: {e}")
    except Exception as e: errs.append(f"borough breakdown: {e}")
    # trend
    if c.get("date"):
        d = c["date"]
        try:
            if c.get("yearly"):
                gb = f"date_trunc_y({d})"; rng = None
            else:
                a, b = c.get("months", M24); rng = f"{d} >= '{a}' and {d} < '{b}'"; gb = f"date_trunc_ym({d})"
            base_w = AND(rng, f"{d} > '1901-01-01'")
            tot = run({"$select": f"{gb} as p, count(*) as n", "$where": base_w, "$group": gb, "$order": "p", "$limit": 500}, "trend")
            mnr = {} if not W or W == "false" else {x["p"]: int(x["n"]) for x in run({"$select": f"{gb} as p, count(*) as n", "$where": AND(base_w, W), "$group": gb, "$order": "p", "$limit": 500}, "trend mn")}
            k = 4 if c.get("yearly") else 7
            P["monthly"] = [[x["p"][:k], int(x["n"]), (mnr.get(x["p"], 0) if (W and W != "false") else None)] for x in tot if "p" in x]
            if c.get("yearly"): P["monthlyTitle"] = "Records per year"; P["monthlyUnit"] = "records per year"
            if c.get("mnall"): P["monthly"] = [[a, b, None] for a, b, _ in P["monthly"]]
        except Exception as e: errs.append(f"trend: {e}")
    # tops
    for t in c.get("tops", []):
        f, title = t[0], t[1]; tw = t[2] if len(t) > 2 else None; dist = t[3] if len(t) > 3 else None
        try:
            sel = f"{f} as k, count(distinct {dist}) as n" if dist else f"{f} as k, count(*) as n"
            r = run({"$select": sel, "$where": AND(W if W != "false" else None, scope, tw, f"{f} is not null"), "$group": f, "$order": "n desc", "$limit": 12}, "top")
            rows = [[x.get("k"), int(x["n"])] for x in r]
            if f == "landuse": rows = [[c["landuse"].get(str(k), k), n] for k, n in rows]
            P["top"].append(dict(title=title, rows=rows, note=("distinct restaurants" if dist else None)))
        except Exception as e: errs.append(f"top {f}: {e}")
    if c.get("builtDecades"):
        try:
            r = run({"$select": "yearbuilt as k, count(*) as n", "$where": AND(W, "yearbuilt >= 1800"), "$group": "yearbuilt", "$limit": 1000}, "built")
            dec = {}
            for x in r: d10 = int(float(x["k"])) // 10 * 10; dec[d10] = dec.get(d10, 0) + int(x["n"])
            P["top"].append(dict(title="Manhattan lots by decade built", rows=[[f"{k}s", v] for k, v in sorted(dec.items())], sorted=True))
        except Exception as e: errs.append(f"decades: {e}")
    for e_ in c.get("extra", []):
        expr, label, dp = e_[:3]; ew = e_[3] if len(e_) > 3 else None
        try:
            r = run({"$select": f"{expr} as v", "$where": AND(None if (c.get("noMn") or W == "false") else W, scope, ew)} if AND(None if (c.get("noMn") or W == "false") else W, scope, ew) else {"$select": f"{expr} as v"}, "extra")
            v = float(r[0]["v"]); P["extra"].append(dict(v=(str(int(v)) if dp == "y" else f"{v:,.{dp}f}" if dp else f"{round(v):,}"), l=label))
        except Exception as e: errs.append(f"{label}: {e}")
    # sample
    try:
        sw = AND(W if W != "false" else None, f"{c['date']} < '2026-11-13'" if c.get("date") else None)
        params = {"$select": ",".join(c["sample"]), "$order": c["order"], "$limit": 25}
        if sw: params["$where"] = sw
        P["sample"] = [{k: (str(v)[:80].replace("T00:00:00.000", "") if not isinstance(v, dict) else "") for k, v in x.items()} for x in run(params, "sample")]
        P["sampleCols"] = c["sample"]
    except Exception as e: errs.append(f"sample: {e}")
    if errs: P["error"] = "; ".join(errs)
    print(i, "done", "ERR " + P.get("error", "") if errs else "", flush=True)
    return P

if __name__ == "__main__":
    only = sys.argv[1:]
    cfg = [c for c in C if not only or c["id"] in only]
    with cf.ThreadPoolExecutor(4) as ex: res = list(ex.map(profile, cfg))
    out = {"fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try: old = {d["id"]: d for d in json.load(open("data/profiles_socrata.json"))["datasets"]}
    except Exception: old = {}
    for r in res: old[r["id"]] = r
    order = [c["id"] for c in C]
    out["datasets"] = sorted(old.values(), key=lambda d: order.index(d["id"]) if d["id"] in order else 99)
    json.dump(out, open("data/profiles_socrata.json", "w"))
