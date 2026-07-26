# -*- coding: utf-8 -*-
"""基本面数据管线（低频，每天1次；新增标的后可改本文件触发补数）：Yahoo quoteSummary 逐标的抓 15 项基本面，
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
def nival(d):
    # 优先归母（applicable to common），缺则退净利总额
    v=raw(d,"netIncomeApplicableToCommonShares")
    return v if v is not None else raw(d,"netIncome")
def yoy(cur,prev):
    # 仅在上期为正时算同比，避免负基数/零基数给出误导性增速
    if cur is None or prev is None or prev<=0: return None
    return round(cur/prev-1,3)
items={}; rats={}   # rats = 机构评级共识，写独立文件 data/ratings.json（可整体下架，不影响其它）
for c,y,cur in targets:
    try:
        u=("https://query1.finance.yahoo.com/v10/finance/quoteSummary/"+urllib.parse.quote(y)
           +"?modules=summaryDetail,defaultKeyStatistics,financialData,incomeStatementHistory,incomeStatementHistoryQuarterly,recommendationTrend,earningsTrend,upgradeDowngradeHistory&crumb="+urllib.parse.quote(crumb))
        r=json.load(op.open(u,timeout=20))["quoteSummary"]["result"]
        if not r: continue
        r=r[0]; sd=r.get("summaryDetail",{}); dk=r.get("defaultKeyStatistics",{}); fd=r.get("financialData",{})
        cash=raw(fd,"totalCash"); debt=raw(fd,"totalDebt")
        # 年报（近4财年，[0]=最近财年）与季报（[0]=最近季度）序列
        ish=r.get("incomeStatementHistory",{}).get("incomeStatementHistory",[]) or []
        ishq=r.get("incomeStatementHistoryQuarterly",{}).get("incomeStatementHistory",[]) or []
        z=lambda v: (None if v==0 else v)   # Yahoo 对A股季度净利额常返回字面0，是缺失哨兵，清成 None
        revA=z(raw(ish[0],"totalRevenue")) if len(ish)>=1 else None
        niA =z(nival(ish[0]))              if len(ish)>=1 else None
        revYoYa=yoy(revA, z(raw(ish[1],"totalRevenue"))) if len(ish)>=2 else None
        niYoYa =yoy(niA,  z(nival(ish[1])))              if len(ish)>=2 else None
        revQ=z(raw(ishq[0],"totalRevenue")) if len(ishq)>=1 else None
        niQ =z(nival(ishq[0]))              if len(ishq)>=1 else None
        rec={"cur":(fd.get("financialCurrency") or cur),"pe":raw(sd,"trailingPE"),"pb":raw(dk,"priceToBook"),"ps":raw(sd,"priceToSalesTrailing12Months"),
          "ev":raw(dk,"enterpriseValue"),"evEbitda":raw(dk,"enterpriseToEbitda"),
          "roe":raw(fd,"returnOnEquity"),"fcf":raw(fd,"freeCashflow"),
          "cash":cash,"debt":debt,"netCash":(cash-debt if cash is not None and debt is not None else None),
          "ebitda":raw(fd,"ebitda"),"rev":raw(fd,"totalRevenue"),
          "revG":raw(fd,"revenueGrowth"),"earnG":raw(fd,"earningsGrowth"),
          "revA":revA,"niA":niA,"revYoYa":revYoYa,"niYoYa":niYoYa,"revQ":revQ,"niQ":niQ,
          "pm":raw(fd,"profitMargins"),"gm":raw(fd,"grossMargins"),"om":raw(fd,"operatingMargins")}
        if any(rec[k] is not None for k in ("pe","pb","ev","roe","rev")):
            items[c]=rec
        # —— 机构评级共识（2026-07-26 新增）——
        # 独立 try：这一段无论怎么炸，都不能影响上面 fundamentals 的产出。
        # 数据同源同一次请求，零额外网络开销。
        try:
            rt=(r.get("recommendationTrend",{}).get("trend") or [])
            cur=next((x for x in rt if x.get("period")=="0m"), rt[0] if rt else None)
            dist=None
            if cur:
                dist=[cur.get("strongBuy"),cur.get("buy"),cur.get("hold"),cur.get("sell"),cur.get("strongSell")]
                dist=[int(x or 0) for x in dist]
                if sum(dist)==0: dist=None
            # 一致预期 EPS 的时间切片：当前/7天/30天/60天/90天（Yahoo 自带，无需我们存历史）
            tr=(r.get("earningsTrend",{}).get("trend") or [])
            def pick(pd):
                for x in tr:
                    if x.get("period")==pd: return x
                return None
            eps={}
            for key,pd in (("y0","0y"),("y1","+1y")):
                t=pick(pd)
                if not t: continue
                est=t.get("earningsEstimate",{}) or {}
                rev=t.get("epsTrend",{}) or {}
                rv =t.get("epsRevisions",{}) or {}
                row={"n":raw(est,"numberOfAnalysts"),"avg":raw(est,"avg"),
                     "low":raw(est,"low"),"high":raw(est,"high"),
                     "d7":raw(rev,"7daysAgo"),"d30":raw(rev,"30daysAgo"),
                     "d60":raw(rev,"60daysAgo"),"d90":raw(rev,"90daysAgo"),
                     "up30":raw(rv,"upLast30days"),"dn30":raw(rv,"downLast30days"),
                     "up7":raw(rv,"upLast7days"),"dn7":raw(rv,"downLast7days"),
                     "yr":t.get("endDate")}
                if row["avg"] is not None: eps[key]=row
            # 最近一次评级变动（用于新鲜度锚 + 徽标高亮方向）
            hist=(r.get("upgradeDowngradeHistory",{}).get("history") or [])
            hist=[h for h in hist if h.get("epochGradeDate")]
            hist.sort(key=lambda h:h["epochGradeDate"], reverse=True)
            recent=[{"d":datetime.date.fromtimestamp(h["epochGradeDate"]).isoformat(),
                     "f":h.get("firm"),"to":h.get("toGrade"),"fr":h.get("fromGrade"),
                     "a":h.get("action")} for h in hist[:6]]
            rr={"tp":raw(fd,"targetMeanPrice"),"tph":raw(fd,"targetHighPrice"),
                "tpl":raw(fd,"targetLowPrice"),"tpm":raw(fd,"targetMedianPrice"),
                "na":raw(fd,"numberOfAnalystOpinions"),
                "rk":fd.get("recommendationKey"),"rm":raw(fd,"recommendationMean"),
                "cur":(fd.get("financialCurrency") or cur),
                "dist":dist,"eps":eps or None,"hist":recent or None,
                "last":(recent[0]["d"] if recent else None)}
            if rr["tp"] is not None or rr["dist"] or rr["eps"]:
                rats[c]=rr
        except Exception:
            pass
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

# —— 独立写出 data/ratings.json（2026-07-26 新增）——
# 与 fundamentals.json 完全解耦：删掉本段 + 删掉该文件，即彻底下架，不影响任何既有功能。
try:
    oldr={}
    if os.path.exists("data/ratings.json"):
        try: oldr=json.load(open("data/ratings.json")).get("items",{})
        except: pass
    if oldr and len(rats)<len(oldr)*0.3:
        print(f"::warning::评级仅 {len(rats)} 条，不足旧 {len(oldr)} 三成，保留旧文件")
    else:
        json.dump({"asof":datetime.date.today().isoformat(),"n":len(rats),"items":rats},
                  open("data/ratings.json","w"),ensure_ascii=False,separators=(",",":"))
        print("ratings ok",len(rats),"条")
except Exception as e:
    print("::warning::ratings 写出失败（不影响基本面）：",e)
