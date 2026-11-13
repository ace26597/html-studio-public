import json, urllib.request, time, sys
ids="uvpi-gqnh erm2-nwe9 nc67-uf89 pvqr-7yc4 693u-uax6 h9gi-nx95 5uac-w243 uip8-fykc 5ucz-vwe8 43nn-pn8j p937-wjvj vfnx-vebw usep-8jbt 64uk-42ks yjub-udmw tg4x-b46p nu7n-tubp rsgh-akpg 6z8x-wfk4 ipu4-2q9a ygpa-z7cr 592z-n7dk wvxf-dwi5 4b4i-vvec hxx3-bwgv uiay-nctu".split()
def get(u):
    for t in range(4):
        try: return json.load(urllib.request.urlopen(u,timeout=60))
        except Exception as e: err=e; time.sleep(3)
    raise err
meta={}
for i in ids:
    try:
        v=get(f"https://data.cityofnewyork.us/api/views/{i}.json")
        cols=[dict(field=c["fieldName"],name=c["name"],type=c["dataTypeName"],desc=(c.get("description") or "")[:200]) for c in v["columns"] if not c["fieldName"].startswith(":@")]
        cnt=get(f"https://data.cityofnewyork.us/resource/{i}.json?$select=count(*)%20as%20n")[0]["n"]
        meta[i]=dict(id=i,name=v["name"],desc=v.get("description",""),agency=v.get("attribution"),category=v.get("category"),
          updated=v.get("rowsUpdatedAt"),freq=(v.get("metadata",{}).get("custom_fields",{}).get("Update",{}) or {}).get("Update Frequency"),
          rows=int(cnt),columns=cols,license=(v.get("license") or {}).get("name"))
        print(i,v["name"][:50],cnt,"|"," ".join(c["field"] for c in cols)[:400])
    except Exception as e:
        print(i,"FAILED",e); meta[i]=dict(id=i,error=str(e))
json.dump(meta,open("data/meta.json","w"),indent=1)
