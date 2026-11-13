"""Fill the Sources list in index.html with every source cited by a ref link, in order of first citation."""
import json, re, pathlib, html
root = pathlib.Path(__file__).resolve().parent.parent
page = (root / "index.html").read_text()
idx = json.loads((root / "sources/index.json").read_text())
items = idx if isinstance(idx, list) else idx.get("sources", idx)
items = items if isinstance(items, list) else list(items.values())
by = {s["id"]: s for s in items}
TITLES = {  # clean titles where the saved title is a URL or carries page chrome
 "s1": ("Kelley Blue Book Report: Average New-Vehicle Prices Top $51,000, Reach Record High in October", "Cox Automotive"),
 "s2": ("October 2026 Kelley Blue Book Average Transaction Price tables", "Cox Automotive"),
 "s4": ("Gasoline and Diesel Fuel Update", "US Energy Information Administration"),
 "s5": ("Electric Power Monthly, Table 5.6.A", "US Energy Information Administration"),
 "s6": ("AAA Gas Prices: national average", "AAA"),
 "s7": ("Comprehensive Total Cost of Ownership Quantification for Vehicles with Different Size Classes and Powertrains (ANL/ESD-21/4)", "Argonne National Laboratory"),
 "s9": ("FAQs for modification of sections 25C, 25D, 25E, 30C, 30D, 45L, 45W and 179D under the One Big Beautiful Bill", "Internal Revenue Service"),
 "s10": ("Short-Term Energy Outlook, November 2026", "US Energy Information Administration"),
 "s13": ("FOTW #1190: Battery-Electric Vehicles Have Lower Scheduled Maintenance Costs", "US Department of Energy"),
 "s14": ("The Top 25 Cars That Hold Their Value Best - and the 25 Worst", "iSeeCars"),
 "s15": ("The Hidden EV Cost Nobody Talks About: Insurance", "Insurify"),
 "s16": ("EV Insurance: Costs, Coverage, and Cheap Quotes", "Insurify"),
 "s17": ("Short-Term Energy Outlook: Electricity, Coal, and Renewables", "US Energy Information Administration"),
 "s21": ("AAA: New Vehicle Ownership Costs Top $12,000 Annually (15 Sept 2026)", "AAA"),
 "s26": ("Publication 6126: Purchased a New Vehicle?", "Internal Revenue Service"),
 "s27": ("Your Driving Costs 2025 fact sheet", "AAA"),
 "s33": ("EV Market Monitor – September 2026", "Cox Automotive"),
 "s35": ("Electric vehicle owners spending half as much on maintenance", "Consumer Reports"),
 "s38": ("How Much Does Electric Car Insurance Cost?", "ValuePenguin (LendingTree)"),
 "s47": ("Colorado's Electric Vehicle Tax Credits", "Colorado Energy Office"),
 "s51": ("Your Driving Costs 2026 (brochure)", "AAA"),
 "s53": ("Compare side-by-side: 2026 Equinox EV, Equinox, Ioniq 5, Tucson", "fueleconomy.gov (EPA/DOE)"),
 "s55": ("Compare side-by-side: 2026 Model Y, RAV4, Model 3, Camry", "fueleconomy.gov (EPA/DOE)"),
 "s59": ("2026 Fuel Economy Guide", "fueleconomy.gov (EPA/DOE)"),
 "s65": ("Maintenance and Safety of Electric Vehicles", "DOE Alternative Fuels Data Center"),
 "s68": ("Charge Up New Jersey", "New Jersey Board of Public Utilities"),
 "s69": ("2027 Chevrolet Equinox EV Review, Pricing, and Specs", "Car and Driver"),
 "s70": ("2027 Chevrolet Equinox Review, Pricing, and Specs", "Car and Driver"),
 "s71": ("The Cheapest Cars to Insure in 2026", "ValuePenguin (LendingTree)"),
 "s72": ("Drive Clean Rebate for Electric Cars Program", "NYSERDA"),
 "s76": ("2027 Tesla Model Y Review, Pricing, and Specs", "Car and Driver"),
 "s77": ("2027 Toyota RAV4 Review, Pricing, and Specs", "Car and Driver"),
 "s78": ("2027 Hyundai Ioniq 5 Review, Pricing, and Specs", "Car and Driver"),
 "s79": ("2027 Hyundai Tucson: What We Know So Far", "Car and Driver"),
 "s80": ("Special Fees on Plug-In Hybrid and Electric Vehicles", "National Conference of State Legislatures"),
 "s81": ("US EV Fast Charging — Q3 2026", "Paren"),
 "s84": ("MOR-EV Rebate Program", "Commonwealth of Massachusetts"),
 "s85": ("2027 Tesla Model 3 Review, Pricing, and Specs", "Car and Driver"),
 "s86": ("2027 Toyota Camry Review, Pricing, and Specs", "Car and Driver"),
 "s87": ("MOR-EV: Cars overview", "MOR-EV.org (Massachusetts DOER program site)"),
 "s88": ("EV drivers will pay $130 a year under Congress' 2026 transportation bill", "Ars Technica"),
 "s90": ("New Data: How Long Do Electric Car Batteries Last?", "Recurrent"),
 "s91": ("FOTW #1335: Level 1 and Level 2 EV Charging is Expected to Account for 80%", "US Department of Energy"),
 "s92": ("EV Battery Health: Key Findings from 22,700 Vehicle Data Analysis", "Geotab"),
 "s93": ("Congress considers $130 annual federal EV fee: What drivers should know", "USA Today"),
 "s96": ("DEEP Announces Increased Incentive Levels for CHEAPR Program Effective October 15th", "Connecticut DEEP"),
 "s97": ("EV Savings fact sheet (June 2023)", "Consumer Reports (copy hosted by 3C Energy)"),
 "s100": ("Electric Car Battery Replacement Costs", "Recurrent"),
 "s101": ("Mitchell EV Collision Insights Report (Q2 2026)", "FenderBender"),
 "s104": ("2025 Vincentric US EV Cost of Ownership Analysis, Summary Report", "Vincentric"),
 "s105": ("Tax credits or no tax credits, EV costs are projected to keep dropping", "ICCT"),
 "s107": ("For most US drivers, EVs offer emissions benefits and cost savings", "MIT News"),
 "s109": ("Satisfaction gap widens between EV and ICE tires", "WardsAuto"),
 "s111": ("California launches MyFirstEV instant rebate program for first-time buyers", "California Air Resources Board"),
 "s112": ("Oregon Clean Vehicle Rebate Program Now Reopen Through November 4, 2026", "Oregon DEQ (Go Electric Oregon)"),
 "s113": ("WAC 458-20-27901: Clean alternative fuel vehicles", "Washington State Legislature"),
 "s114": ("Xcel Energy EV Charging Rebates & Incentives", "WeaveGrid (Xcel Energy program partner)"),
 "s115": ("Illinois Electric Vehicle Rebate Program application cycle now open", "NPR Illinois"),
}
body = page.split("<!--SOURCES-->")[0] if "<!--SOURCES-->" in page else page
order = []
for m in re.finditer(r'href="#src-(s\d+)"', page):
    if m.group(1) not in order: order.append(m.group(1))
for m in re.finditer(r'data-src="([^"]+)"', page):
    for sid in m.group(1).split():
        if re.fullmatch(r"s\d+", sid) and sid not in order: order.append(sid)
lis = []
for sid in order:
    s = by[sid]
    t, pub = TITLES.get(sid, (s.get("title") or s["url"], ""))
    date = (s.get("fetched_at") or "")[:10]
    lis.append(f'<li id="src-{sid}"><strong>{html.escape(t)}</strong>{" — " + html.escape(pub) if pub else ""}. '
               f'<a href="{html.escape(s["url"])}">{html.escape(s["url"])}</a>. Saved {date}. <span class="pill">{sid}</span></li>')
new = re.sub(r'(<ol class="sources" id="source-list"[^>]*>).*?(</ol>)', lambda m: m.group(1) + "<!--SOURCES-->\n" + "\n".join(lis) + "\n" + m.group(2), page, flags=re.S)
(root / "index.html").write_text(new)
missing = [s for s in order if s not in TITLES]
print(len(order), "sources listed; missing clean titles:", missing)
