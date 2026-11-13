import json, urllib.request, time
out=[]; off=0
while True:
    u=f"https://api.us.socrata.com/api/catalog/v1?domains=data.cityofnewyork.us&limit=1000&offset={off}"
    for t in range(4):
        try:
            d=json.load(urllib.request.urlopen(u,timeout=60)); break
        except Exception as e: print("retry",e); time.sleep(3)
    r=d["results"]
    for x in r:
        res=x["resource"]; c=x.get("classification",{})
        out.append(dict(id=res["id"],name=res["name"],type=res.get("type"),desc=(res.get("description") or "")[:600],
          agency=res.get("attribution"),updated=res.get("data_updated_at") or res.get("updatedAt"),
          created=res.get("createdAt"),views=(res.get("page_views") or {}).get("page_views_total"),
          downloads=res.get("download_count"),category=c.get("domain_category"),tags=c.get("domain_tags",[])[:8],
          cols=len(res.get("columns_name") or [])))
    print(off,len(r),d.get("resultSetSize"))
    off+=1000
    if off>=d["resultSetSize"]: break
json.dump(dict(fetched=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),total=len(out),datasets=out),open("data/catalog.json","w"))
