import json,urllib.request,urllib.parse,ssl,datetime
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
UA={'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
def chart(sym,rng):
    u=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range={rng}&interval=1d"
    req=urllib.request.Request(u,headers=UA)
    d=json.load(urllib.request.urlopen(req,timeout=20,context=ctx))
    res=d['chart']['result'][0]
    return res['timestamp'], res['indicators']['quote'][0]['close']
out={}
target=datetime.date(2026,7,16)
for sym in ['^W5000','^FTW5000','^RUA','^NYA','^GSPC','^DWCF']:
    rec={}
    try:
        ts,cl=chart(sym,'5d'); vals=[c for c in cl if c]
        rec['last5d']=round(vals[-1],2) if vals else None; rec['n5d']=len(vals)
    except Exception as e: rec['err5d']=str(e)[:50]
    try:
        ts,cl=chart(sym,'1y'); best=None
        for t,c in zip(ts,cl):
            if c is None: continue
            dd=datetime.datetime.utcfromtimestamp(t).date()
            if abs((dd-target).days)<=4 and (best is None or abs((dd-target).days)<abs((best[0]-target).days)): best=(dd,c)
        rec['near0716']=[str(best[0]),round(best[1],2)] if best else None
    except Exception as e: rec['err1y']=str(e)[:50]
    out[sym]=rec
json.dump(out,open('data/probe_idx.json','w'),ensure_ascii=False,indent=1)
print("done")
