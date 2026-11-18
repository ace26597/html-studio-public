"""The GLP-1 race - back end.

Fetches live, free public data and caches it in state/:
  * ClinicalTrials.gov API v2  (trials that list each drug as an intervention)
  * openFDA drug/event         (FAERS adverse-event report counts, approved drugs)
  * openFDA drug/label         (US product labels)

Nothing here invents a number. Every fetch is logged with its URL, time and
outcome; when a fetch fails the previous good copy is kept but marked stale,
and the page is told so.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).parent
STATE = ROOT / "state"
STATE.mkdir(exist_ok=True)
CACHE_FILE = STATE / "cache.json"

CTGOV = "https://clinicaltrials.gov/api/v2/studies"
FDA_EVENT = "https://api.fda.gov/drug/event.json"
FDA_LABEL = "https://api.fda.gov/drug/label.json"
UA = {"User-Agent": "glp1-race-dashboard/1.0 (public-data dashboard)"}

CT_TTL = 12 * 3600       # re-fetch trials every 12 h
FDA_TTL = 24 * 3600      # re-fetch openFDA every 24 h
MANUAL_MIN_GAP = 15 * 60  # manual refresh at most every 15 min

# id, display name, ClinicalTrials.gov intervention query, openFDA generic name,
# whether to pull FAERS reports (the four approved drugs the dashboard covers)
DRUGS = [
    {"id": "semaglutide", "name": "Semaglutide", "ct": "semaglutide OR Ozempic OR Wegovy OR Rybelsus",
     "fda": "semaglutide", "faers": True},
    {"id": "tirzepatide", "name": "Tirzepatide", "ct": "tirzepatide OR LY3298176 OR Mounjaro OR Zepbound",
     "fda": "tirzepatide", "faers": True},
    {"id": "liraglutide", "name": "Liraglutide", "ct": "liraglutide OR Victoza OR Saxenda",
     "fda": "liraglutide", "faers": True},
    {"id": "dulaglutide", "name": "Dulaglutide", "ct": "dulaglutide OR Trulicity",
     "fda": "dulaglutide", "faers": True},
    {"id": "retatrutide", "name": "Retatrutide", "ct": "retatrutide OR LY3437943",
     "fda": "retatrutide", "faers": False},
    {"id": "orforglipron", "name": "Orforglipron", "ct": "orforglipron OR LY3502970",
     "fda": "orforglipron", "faers": False},
    {"id": "survodutide", "name": "Survodutide", "ct": 'survodutide OR "BI 456906"',
     "fda": "survodutide", "faers": False},
    {"id": "cagrilintide", "name": "Cagrilintide (incl. CagriSema)", "ct": "cagrilintide OR CagriSema",
     "fda": "cagrilintide", "faers": False},
    {"id": "maridebart", "name": "Maridebart cafraglutide", "ct": '"maridebart cafraglutide" OR "AMG 133" OR MariTide',
     "fda": "maridebart cafraglutide", "faers": False},
]
DRUG_IDS = {d["id"] for d in DRUGS}

CT_FIELDS = ",".join([
    "NCTId", "BriefTitle", "Acronym", "OverallStatus", "Phase", "StudyType",
    "LeadSponsorName", "LeadSponsorClass", "CollaboratorName", "EnrollmentCount", "EnrollmentType",
    "StartDate", "PrimaryCompletionDate", "PrimaryCompletionDateType", "CompletionDate",
    "Condition", "InterventionName", "LocationCountry", "LocationCity", "LocationGeoPoint",
    "LastUpdatePostDate", "HasResults",
])


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ----------------------------------------------------------------- cache ---
cache: dict = {"ct": {}, "fda": {}, "labels": {}, "reactions": {}, "trial": {}, "log": []}
if CACHE_FILE.exists():
    try:
        cache.update(json.loads(CACHE_FILE.read_text()))
    except Exception:  # a broken cache file is simply ignored
        pass
cache.setdefault("trial", {})
cache.setdefault("reactions", {})
_lock = asyncio.Lock()
_state = {"refreshing": False, "last_manual": 0.0, "assembled": None, "assembled_key": None}


def save_cache() -> None:
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    tmp.replace(CACHE_FILE)


def log_fetch(source: str, label: str, url: str, ok: bool, n=None, error: str | None = None) -> dict:
    entry = {"source": source, "label": label, "url": url, "at": now_iso(), "ok": ok, "n": n, "error": error}
    cache["log"] = ([entry] + cache.get("log", []))[:200]
    return entry


def ts(iso: str | None) -> float:
    if not iso:
        return 0
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0


# --------------------------------------------------------- ClinicalTrials ---
async def fetch_ct_drug(client: httpx.AsyncClient, drug: dict) -> None:
    params = {"query.intr": drug["ct"], "fields": CT_FIELDS, "pageSize": 1000, "countTotal": "true", "format": "json"}
    public_url = f"{CTGOV}?{urlencode({'query.intr': drug['ct'], 'pageSize': 1000, 'countTotal': 'true'})}"
    studies, total, token, pages = [], None, None, 0
    prev = cache["ct"].get(drug["id"], {})
    try:
        while True:
            p = dict(params)
            if token:
                p["pageToken"] = token
            r = await client.get(CTGOV, params=p, timeout=60)
            r.raise_for_status()
            j = r.json()
            if total is None:
                total = j.get("totalCount")
            studies.extend(j.get("studies", []))
            token = j.get("nextPageToken")
            pages += 1
            if not token or pages > 20:
                break
        if total is not None and len(studies) < total:
            raise RuntimeError(f"received {len(studies)} of {total} studies")
        cache["ct"][drug["id"]] = {
            "ok": True, "fetched_at": now_iso(), "url": public_url, "total": total,
            "studies": studies, "error": None, "attempted_at": now_iso(),
        }
        log_fetch("ClinicalTrials.gov", f"Trials listing {drug['name']}", public_url, True, total)
    except Exception as e:  # keep old good copy, mark it stale
        msg = f"{type(e).__name__}: {e}"[:300]
        entry = dict(prev) if prev else {"studies": [], "fetched_at": None, "total": None}
        entry.update({"ok": False, "error": msg, "attempted_at": now_iso(), "url": public_url})
        cache["ct"][drug["id"]] = entry
        log_fetch("ClinicalTrials.gov", f"Trials listing {drug['name']}", public_url, False, None, msg)


# ---------------------------------------------------------------- openFDA ---
class FdaNoMatch(Exception):
    pass


async def fda_get(client: httpx.AsyncClient, base: str, params: dict) -> dict:
    for attempt in range(3):
        r = await client.get(base, params=params, timeout=45)
        if r.status_code == 404:
            try:
                if r.json().get("error", {}).get("code") == "NOT_FOUND":
                    raise FdaNoMatch()
            except ValueError:
                pass
        if r.status_code == 429 and attempt < 2:
            await asyncio.sleep(2 + attempt * 3)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def fda_url(base: str, params: dict) -> str:
    return f"{base}?{urlencode(params)}"


async def fetch_fda_drug(client: httpx.AsyncClient, drug: dict) -> None:
    g = drug["fda"]
    q = f'patient.drug.openfda.generic_name:"{g}"'
    out = {"fetched_at": now_iso(), "attempted_at": now_iso(), "ok": True, "errors": [], "queries": {}}
    prev = cache["fda"].get(drug["id"], {})

    async def run(key, params, label):
        url = fda_url(FDA_EVENT, params)
        out["queries"][key] = url
        try:
            j = await fda_get(client, FDA_EVENT, params)
            log_fetch("openFDA", f"{label} - {drug['name']}", url, True)
            return j
        except FdaNoMatch:
            log_fetch("openFDA", f"{label} - {drug['name']}", url, True, 0)
            return {"meta": {"results": {"total": 0}}, "results": []}
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"[:300]
            out["errors"].append(f"{label}: {msg}")
            log_fetch("openFDA", f"{label} - {drug['name']}", url, False, None, msg)
            return None

    tot = await run("total", {"search": q, "limit": 1}, "Total reports")
    ser = await run("serious", {"search": f"{q} AND serious:1", "limit": 1}, "Serious reports")
    by_date = await run("by_date", {"search": q, "count": "receivedate"}, "Reports by receipt date")
    top = await run("top", {"search": q, "count": "patient.reaction.reactionmeddrapt.exact", "limit": 25},
                    "Most reported reactions")

    if tot is not None:
        out["total"] = tot.get("meta", {}).get("results", {}).get("total")
        out["last_updated"] = tot.get("meta", {}).get("last_updated")
    if ser is not None:
        out["serious"] = ser.get("meta", {}).get("results", {}).get("total")
    if by_date is not None:
        out["by_year"], out["last_receive_date"] = by_year(by_date.get("results", []))
    if top is not None:
        out["top"] = [{"term": r["term"], "count": r["count"]} for r in top.get("results", [])]
    if out["errors"]:
        out["ok"] = False
        # fill gaps from the previous good copy, flagged
        for k in ("total", "serious", "by_year", "top", "last_updated", "last_receive_date"):
            if k not in out and k in prev:
                out[k] = prev[k]
                out.setdefault("stale_fields", []).append(k)
        out["stale_from"] = prev.get("fetched_at")
        out["fetched_at"] = prev.get("fetched_at")
    cache["fda"][drug["id"]] = out


def by_year(rows: list) -> tuple[list, str | None]:
    years: dict[str, int] = {}
    last = None
    for r in rows:
        t = r.get("time", "")
        if len(t) >= 4:
            years[t[:4]] = years.get(t[:4], 0) + int(r.get("count", 0))
            if last is None or t > last:
                last = t
    return [{"year": int(y), "count": c} for y, c in sorted(years.items())], last


async def fetch_label(client: httpx.AsyncClient, drug: dict) -> None:
    params = {"search": f'openfda.generic_name:"{drug["fda"]}"', "limit": 100}
    url = fda_url(FDA_LABEL, params)
    try:
        try:
            j = await fda_get(client, FDA_LABEL, params)
            results = j.get("results", [])
        except FdaNoMatch:
            results = []
        brands: dict[str, dict] = {}
        for r in results:
            o = r.get("openfda", {})
            for b in o.get("brand_name", []) or ["(no brand)"]:
                key = b.strip().upper()
                rec = {
                    "brand": b.strip().title() if b.isupper() else b.strip(),
                    "manufacturer": (o.get("manufacturer_name") or [""])[0],
                    "effective": r.get("effective_time"),
                    "set_id": r.get("set_id"),
                    "route": (o.get("route") or [""])[0],
                    "boxed_warning": (r.get("boxed_warning") or [""])[0][:600],
                    "indications": (r.get("indications_and_usage") or [""])[0][:700],
                }
                cur = brands.get(key)
                # prefer the most recent label for each brand
                if cur is None or (rec["effective"] or "") > (cur["effective"] or ""):
                    brands[key] = rec
        cache["labels"][drug["id"]] = {"ok": True, "fetched_at": now_iso(), "url": url,
                                       "n_labels": len(results), "brands": sorted(brands.values(), key=lambda x: x["brand"]),
                                       "error": None}
        log_fetch("openFDA", f"Drug labels - {drug['name']}", url, True, len(results))
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:300]
        prev = cache["labels"].get(drug["id"], {})
        entry = dict(prev) if prev else {"brands": [], "fetched_at": None}
        entry.update({"ok": False, "error": msg, "url": url, "attempted_at": now_iso()})
        cache["labels"][drug["id"]] = entry
        log_fetch("openFDA", f"Drug labels - {drug['name']}", url, False, None, msg)


# ------------------------------------------------------------- refreshing ---
RETRY_AFTER = 30 * 60


def has_label(d: dict) -> bool:
    return bool((cache["labels"].get(d["id"]) or {}).get("brands"))


def wants_faers(d: dict) -> bool:
    """FAERS is pulled for the four approved drugs and for any drug that now has a US label on openFDA."""
    return d["faers"] or has_label(d)


def due(entry: dict | None, ttl: int) -> bool:
    """A source is due when never fetched, older than its TTL, or failed more than 30 min ago."""
    if not entry or not entry.get("fetched_at"):
        return time.time() - ts((entry or {}).get("attempted_at")) > RETRY_AFTER if entry else True
    if entry.get("ok") is False:
        return time.time() - ts(entry.get("attempted_at")) > RETRY_AFTER
    return time.time() - ts(entry.get("fetched_at")) > ttl

async def refresh(force: bool = False) -> None:
    if _state["refreshing"]:
        return
    _state["refreshing"] = True
    try:
        async with httpx.AsyncClient(headers=UA, follow_redirects=True) as client:
            ct_due = [d for d in DRUGS if force or due(cache["ct"].get(d["id"]), CT_TTL)]
            # CT.gov: a few at a time
            for i in range(0, len(ct_due), 3):
                await asyncio.gather(*(fetch_ct_drug(client, d) for d in ct_due[i:i + 3]))
            _state["assembled"] = None
            save_cache()
            for d in DRUGS:
                if force or due(cache["labels"].get(d["id"]), FDA_TTL):
                    await fetch_label(client, d)
            for d in DRUGS:
                if wants_faers(d) and (force or due(cache["fda"].get(d["id"]), FDA_TTL)):
                    await fetch_fda_drug(client, d)
            if force:
                cache["reactions"] = {}
            save_cache()
    finally:
        _state["refreshing"] = False


async def scheduler():
    while True:
        try:
            await refresh()
        except Exception as e:
            log_fetch("app", "Scheduled refresh", "", False, None, str(e)[:300])
        await asyncio.sleep(30 * 60)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, title="The GLP-1 race")
_allowed = os.environ.get("STUDIO_ALLOWED_HOSTS")
if _allowed:
    hosts = [h.strip().rsplit(":", 1)[0] if h.strip().count(":") == 1 else h.strip()
             for h in _allowed.split(",") if h.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
app.add_middleware(GZipMiddleware, minimum_size=1000)


# --------------------------------------------------------- normalisation ---
PHASE_LABEL = {
    ("EARLY_PHASE1",): "Early phase 1", ("PHASE1",): "Phase 1", ("PHASE1", "PHASE2"): "Phase 1/2",
    ("PHASE2",): "Phase 2", ("PHASE2", "PHASE3"): "Phase 2/3", ("PHASE3",): "Phase 3",
    ("PHASE4",): "Phase 4", ("NA",): "Not applicable",
}


def phase_label(phases: list) -> str:
    if not phases:
        return "No phase (observational/other)"
    return PHASE_LABEL.get(tuple(sorted(phases)), "/".join(phases))


def assemble() -> dict:
    """Merge per-drug trial lists into one de-duplicated list with a site table."""
    key = tuple((d["id"], cache["ct"].get(d["id"], {}).get("fetched_at")) for d in DRUGS)
    if _state["assembled"] is not None and _state["assembled_key"] == key:
        return _state["assembled"]
    trials: dict[str, dict] = {}
    site_index: dict[tuple, int] = {}
    sites: list = []
    for d in DRUGS:
        for s in cache["ct"].get(d["id"], {}).get("studies", []):
            p = s.get("protocolSection", {})
            idm, st, sp = p.get("identificationModule", {}), p.get("statusModule", {}), p.get("sponsorCollaboratorsModule", {})
            de, cl = p.get("designModule", {}), p.get("contactsLocationsModule", {})
            nct = idm.get("nctId")
            if not nct:
                continue
            if nct in trials:
                if d["id"] not in trials[nct]["drugs"]:
                    trials[nct]["drugs"].append(d["id"])
                continue
            locs = cl.get("locations", []) or []
            sidx, countries = [], []
            for loc in locs:
                c = loc.get("country")
                if c and c not in countries:
                    countries.append(c)
                gp = loc.get("geoPoint")
                if gp and gp.get("lat") is not None:
                    k = (loc.get("city") or "", c or "", round(gp["lat"], 2), round(gp["lon"], 2))
                    if k not in site_index:
                        site_index[k] = len(sites)
                        sites.append(list(k))
                    i = site_index[k]
                    if i not in sidx:
                        sidx.append(i)
            enr = de.get("enrollmentInfo", {}) or {}
            trials[nct] = {
                "id": nct,
                "title": idm.get("briefTitle"),
                "acronym": idm.get("acronym"),
                "status": st.get("overallStatus"),
                "phases": de.get("phases", []) or [],
                "phase": phase_label(de.get("phases", []) or []),
                "type": de.get("studyType"),
                "sponsor": (sp.get("leadSponsor") or {}).get("name"),
                "sponsor_class": (sp.get("leadSponsor") or {}).get("class"),
                "collaborators": [c.get("name") for c in sp.get("collaborators", []) or []],
                "enrollment": enr.get("count"),
                "enrollment_type": enr.get("type"),
                "start": (st.get("startDateStruct") or {}).get("date"),
                "primary_completion": (st.get("primaryCompletionDateStruct") or {}).get("date"),
                "primary_completion_type": (st.get("primaryCompletionDateStruct") or {}).get("type"),
                "completion": (st.get("completionDateStruct") or {}).get("date"),
                "last_update": (st.get("lastUpdatePostDateStruct") or {}).get("date"),
                "conditions": (p.get("conditionsModule") or {}).get("conditions", []) or [],
                "interventions": [i.get("name") for i in (p.get("armsInterventionsModule") or {}).get("interventions", []) or []],
                "countries": countries,
                "n_locations": len(locs),
                "sites": sidx,
                "has_results": s.get("hasResults", False),
                "drugs": [d["id"]],
            }
    out = {"trials": list(trials.values()), "sites": sites}
    _state["assembled"], _state["assembled_key"] = out, key
    return out


def ct_sources() -> list:
    res = []
    for d in DRUGS:
        c = cache["ct"].get(d["id"], {})
        res.append({"drug": d["id"], "name": d["name"], "query": d["ct"], "ok": c.get("ok", False),
                    "fetched_at": c.get("fetched_at"), "attempted_at": c.get("attempted_at"),
                    "total": c.get("total"), "received": len(c.get("studies", [])),
                    "url": c.get("url"), "error": c.get("error") if c else "not fetched yet"})
    return res


# ------------------------------------------------------------------- API ---
@app.get("/api/meta")
def api_meta():
    return {"drugs": [{k: d[k] for k in ("id", "name", "ct", "fda", "faers")} for d in DRUGS],
            "refreshing": _state["refreshing"], "server_time": now_iso(),
            "ct": ct_sources()}


@app.get("/api/trials")
def api_trials():
    data = assemble()
    return {"server_time": now_iso(), "refreshing": _state["refreshing"], "sources": ct_sources(),
            "trials": data["trials"], "sites": data["sites"]}


@app.get("/api/fda")
def api_fda():
    out = []
    for d in DRUGS:
        lab = cache["labels"].get(d["id"])
        out.append({"drug": d["id"], "name": d["name"], "faers": wants_faers(d), "core": d["faers"],
                    "has_label": has_label(d),
                    "events": cache["fda"].get(d["id"]) if wants_faers(d) else None,
                    "labels": lab})
    return {"server_time": now_iso(), "refreshing": _state["refreshing"], "drugs": out}


@app.get("/api/fda/reaction")
async def api_reaction(term: str = Query(..., min_length=2, max_length=80)):
    term = term.strip().upper()
    allowed = set()
    for d in DRUGS:
        for r in (cache["fda"].get(d["id"]) or {}).get("top", []) or []:
            allowed.add(r["term"].upper())
    if term not in allowed:
        raise HTTPException(400, "Pick a reaction from the list.")
    hit = cache["reactions"].get(term)
    if hit and time.time() - ts(hit["fetched_at"]) < FDA_TTL and not hit.get("errors"):
        return hit
    res = {"term": term, "fetched_at": now_iso(), "drugs": {}, "errors": [], "urls": {}}
    async with httpx.AsyncClient(headers=UA) as client:
        for d in DRUGS:
            if not wants_faers(d) or d["id"] not in cache["fda"]:
                continue
            params = {"search": f'patient.drug.openfda.generic_name:"{d["fda"]}" AND patient.reaction.reactionmeddrapt.exact:"{term}"',
                      "count": "receivedate"}
            url = fda_url(FDA_EVENT, params)
            res["urls"][d["id"]] = url
            try:
                j = await fda_get(client, FDA_EVENT, params)
                res["drugs"][d["id"]] = by_year(j.get("results", []))[0]
                log_fetch("openFDA", f"Reaction {term} by year - {d['name']}", url, True)
            except FdaNoMatch:
                res["drugs"][d["id"]] = []
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"[:300]
                res["errors"].append(f"{d['name']}: {msg}")
                log_fetch("openFDA", f"Reaction {term} by year - {d['name']}", url, False, None, msg)
    cache["reactions"][term] = res
    save_cache()
    return res


NCT_RE = re.compile(r"^NCT\d{8}$")


@app.get("/api/trial/{nct}")
async def api_trial(nct: str):
    nct = nct.upper()
    if not NCT_RE.match(nct):
        raise HTTPException(400, "Not a ClinicalTrials.gov identifier (NCT followed by 8 digits).")
    hit = cache["trial"].get(nct)
    if hit and time.time() - ts(hit["fetched_at"]) < 6 * 3600:
        return hit
    url = f"{CTGOV}/{nct}"
    try:
        async with httpx.AsyncClient(headers=UA) as client:
            r = await client.get(url, params={"format": "json"}, timeout=40)
            if r.status_code == 404:
                raise HTTPException(404, f"ClinicalTrials.gov has no study {nct}.")
            r.raise_for_status()
            j = r.json()
    except HTTPException:
        raise
    except Exception as e:
        if hit:
            return {**hit, "stale": True, "error": f"{type(e).__name__}: {e}"[:300]}
        return JSONResponse({"error": f"ClinicalTrials.gov did not answer: {type(e).__name__}: {e}"[:300],
                             "url": url}, status_code=502)
    p = j.get("protocolSection", {})
    st, de, ds = p.get("statusModule", {}), p.get("designModule", {}), p.get("descriptionModule", {})
    el, ai, om = p.get("eligibilityModule", {}), p.get("armsInterventionsModule", {}), p.get("outcomesModule", {})
    sp, idm, cl = p.get("sponsorCollaboratorsModule", {}), p.get("identificationModule", {}), p.get("contactsLocationsModule", {})
    locs = cl.get("locations", []) or []
    countries: dict[str, int] = {}
    for l in locs:
        countries[l.get("country") or "Not given"] = countries.get(l.get("country") or "Not given", 0) + 1
    out = {
        "id": nct, "fetched_at": now_iso(), "url": f"https://clinicaltrials.gov/study/{nct}", "api_url": url,
        "title": idm.get("briefTitle"), "official_title": idm.get("officialTitle"), "acronym": idm.get("acronym"),
        "org_id": (idm.get("orgStudyIdInfo") or {}).get("id"),
        "status": st.get("overallStatus"), "why_stopped": st.get("whyStopped"),
        "start": (st.get("startDateStruct") or {}).get("date"),
        "start_type": (st.get("startDateStruct") or {}).get("type"),
        "primary_completion": (st.get("primaryCompletionDateStruct") or {}).get("date"),
        "primary_completion_type": (st.get("primaryCompletionDateStruct") or {}).get("type"),
        "completion": (st.get("completionDateStruct") or {}).get("date"),
        "completion_type": (st.get("completionDateStruct") or {}).get("type"),
        "first_posted": (st.get("studyFirstPostDateStruct") or {}).get("date"),
        "last_update": (st.get("lastUpdatePostDateStruct") or {}).get("date"),
        "phase": phase_label(de.get("phases", []) or []), "type": de.get("studyType"),
        "allocation": (de.get("designInfo") or {}).get("allocation"),
        "masking": ((de.get("designInfo") or {}).get("maskingInfo") or {}).get("masking"),
        "enrollment": (de.get("enrollmentInfo") or {}).get("count"),
        "enrollment_type": (de.get("enrollmentInfo") or {}).get("type"),
        "sponsor": (sp.get("leadSponsor") or {}).get("name"),
        "collaborators": [c.get("name") for c in sp.get("collaborators", []) or []],
        "conditions": (p.get("conditionsModule") or {}).get("conditions", []),
        "summary": ds.get("briefSummary"),
        "arms": [{"label": a.get("label"), "type": a.get("type"), "description": a.get("description")}
                 for a in ai.get("armGroups", []) or []],
        "interventions": [{"type": i.get("type"), "name": i.get("name")} for i in ai.get("interventions", []) or []],
        "primary_outcomes": [{"measure": o.get("measure"), "timeFrame": o.get("timeFrame")}
                             for o in om.get("primaryOutcomes", []) or []],
        "n_secondary_outcomes": len(om.get("secondaryOutcomes", []) or []),
        "eligibility": {"sex": el.get("sex"), "min_age": el.get("minimumAge"), "max_age": el.get("maximumAge"),
                        "healthy": el.get("healthyVolunteers"), "criteria": (el.get("eligibilityCriteria") or "")[:6000]},
        "n_locations": len(locs),
        "countries": sorted(countries.items(), key=lambda x: -x[1]),
        "has_results": j.get("hasResults", False),
    }
    cache["trial"][nct] = out
    if len(cache["trial"]) > 300:
        for k in sorted(cache["trial"], key=lambda k: cache["trial"][k]["fetched_at"])[:100]:
            cache["trial"].pop(k, None)
    log_fetch("ClinicalTrials.gov", f"Study record {nct}", url, True, 1)
    save_cache()
    return out


@app.post("/api/refresh")
async def api_refresh():
    if _state["refreshing"]:
        return {"started": False, "message": "A refresh is already running."}
    gap = time.time() - _state["last_manual"]
    if gap < MANUAL_MIN_GAP:
        return {"started": False, "message": f"Refreshed recently; try again in {int((MANUAL_MIN_GAP - gap) // 60) + 1} min."}
    _state["last_manual"] = time.time()
    asyncio.create_task(refresh(force=True))
    return {"started": True, "message": "Refresh started. New data appears when it finishes."}


@app.get("/api/log")
def api_log():
    return {"log": cache.get("log", [])[:120], "refreshing": _state["refreshing"]}


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
