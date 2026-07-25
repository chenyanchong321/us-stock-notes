import json,urllib.request,urllib.parse,http.cookiejar,ssl
syms={
 "AMD(美)":"AMD","NET(美)":"NET","NVDA(美)":"NVDA",
 "东山002384(A)":"002384.SZ","澜起688008(A)":"688008.SS","紫金601899(A)":"601899.SS",
 "潍柴02338(港)":"2338.HK","中海油00883(港)":"0883.HK",
 "信越4063(日)":"4063.T","住友5802(日)":"5802.T","台积2330(台)":"2330.TW",
}
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
cj=http.cookiejar.CookieJar()
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj),urllib.request.HTTPSHandler(context=ctx))
op.addheaders=[("User-Agent","Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")]
try: op.open("https://fc.yahoo.com",timeout=15).read(0)
except: pass
crumb=op.open("https://query1.finance.yahoo.com/v1/test/getcrumb",timeout=15).read().decode()
Q=["trailingPE","forwardPE","priceToBook","marketCap"]
DKS=["priceToBook","enterpriseValue","enterpriseToEbitda","trailingEps"]
FIN=["returnOnEquity","freeCashflow","totalCash","totalDebt","ebitda","totalRevenue","revenueGrowth","earningsGrowth","profitMargins","grossMargins","operatingMargins"]
out={}
for name,sym in syms.items():
    rec={}
    try:
        u="https://query1.finance.yahoo.com/v7/finance/quote?symbols="+urllib.parse.quote(sym)+"&crumb="+urllib.parse.quote(crumb)
        d=json.load(op.open(u,timeout=20))["quoteResponse"]["result"]; q=d[0] if d else {}
        for f in Q: rec["q."+f]=q.get(f)
    except Exception as e: rec["q.err"]=str(e)[:60]
    try:
        u="https://query1.finance.yahoo.com/v10/finance/quoteSummary/"+urllib.parse.quote(sym)+"?modules=defaultKeyStatistics,financialData&crumb="+urllib.parse.quote(crumb)
        d=json.load(op.open(u,timeout=20))["quoteSummary"]["result"]; r=d[0] if d else {}
        dks=r.get("defaultKeyStatistics",{}); fin=r.get("financialData",{})
        for f in DKS:
            v=dks.get(f); rec["dks."+f]=(v.get("raw") if isinstance(v,dict) else v)
        for f in FIN:
            v=fin.get(f); rec["fin."+f]=(v.get("raw") if isinstance(v,dict) else v)
    except Exception as e: rec["s.err"]=str(e)[:60]
    out[name]=rec
json.dump(out,open("data/probe_fund.json","w"),ensure_ascii=False,indent=1)
print("done")
