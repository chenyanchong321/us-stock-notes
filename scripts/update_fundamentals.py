# -*- coding: utf-8 -*-
"""基本面数据管线（低频，每天1次）：Yahoo quoteSummary 逐标的抓 15 项基本面，
写 data/fundamentals.json，供前端市值浮窗读取。与高频行情/PE 彻底分离，不动行结构。
防呆：crumb 失败或抓到量不足旧文件三成时，保留旧文件（坏数据比旧数据危害大，铁律）。"""
import json,urllib.request,urllib.parse,http.cookiejar,ssl,time,datetime,sys,os
w=json.load(open("config/watchlist.json"))
targets=[]; seen=set()
for s in w["sections"]:
    for it in s["items"]:
        c=it.get("code"); y=it.get("yahoo")
        if not c or not y or c in seen: continue
        if y.startswith("em:") or y.endswith(".EM"): continue
        seen.add(c); targets.append((c,y,it.get("currency","")))
ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
cj=http.cookiejar.CookieJar()
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj),urllib.request.HTTPSHandler(context=ctx))
op.addheaders=[("User-Agent","Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")]
try: op.open("https://fc.yahoo.com",timeout=15).read(0)
except: pass
try: crumb=op.open("https://query1.finance.yahoo.com/v1/test/getcrumb",timeout=15).read().decode()
except Exception as e:
    print("::warning::crumb 失败，保留旧文件：",e); sys.exit(0)
def raw(d,k):
    v=d.get(k)
    return v.get("raw") if isinstance(v,dict) else v
items={}
for c,y,cur in targets:
    try:
        u=("https://query1.finance.yahoo.com/v10/finance/quoteSummary/"+urllib.parse.quote(y)
           +"?modules=summaryDetail,defaultKeyStatistics,financialData&crumb="+urllib.parse.quote(crumb))
        r=json.load(op.open(u,timeout=20))["quoteSummary"]["result"]
        if not r: continue
        r=r[0]; sd=r.get("summaryDetail",{}); dk=r.get("defaultKeyStatistics",{}); fd=r.get("financialData",{})
        cash=raw(fd,"totalCash"); debt=raw(fd,"totalDebt")
        rec={"cur":cur,"pe":raw(sd,"trailingPE"),"pb":raw(dk,"priceToBook"),
          "ev":raw(dk,"enterpriseValue"),"evEbitda":raw(dk,"enterpriseToEbitda"),
          "roe":raw(fd,"returnOnEquity"),"fcf":raw(fd,"freeCashflow"),
          "cash":cash,"debt":debt,"netCash":(cash-debt if cash is not None and debt is not None else None),
          "ebitda":raw(fd,"ebitda"),"rev":raw(fd,"totalRevenue"),
          "revG":raw(fd,"revenueGrowth"),"earnG":raw(fd,"earningsGrowth"),
          "pm":raw(fd,"profitMargins"),"gm":raw(fd,"grossMargins"),"om":raw(fd,"operatingMargins")}
        if any(rec[k] is not None for k in ("pe","pb","ev","roe","rev")):
            items[c]=rec
    except Exception:
        pass
    time.sleep(0.05)
old={}
if os.path.exists("data/fundamentals.json"):
    try: old=json.load(open("data/fundamentals.json")).get("items",{})
    except: pass
if old and len(items)<len(old)*0.3:
    print(f"::warning::本次仅 {len(items)} 条，不足旧 {len(old)} 三成，保留旧文件"); sys.exit(0)
json.dump({"asof":datetime.date.today().isoformat(),"items":items},
          open("data/fundamentals.json","w"),ensure_ascii=False,separators=(",",":"))
print("ok",len(items),"条")
