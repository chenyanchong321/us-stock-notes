#!/usr/bin/env python3
# A股两融余额每日抓取（GitHub Actions runner，2026-07-20）
# 源=东方财富数据中心 RPTA_RZRQ_LSHJ（沪深两融历史汇总，交易所盘后数据的镜像，T+1）
# 产出 data/margin.json：近250个交易日 {d,rzye融资余额亿,lr两融余额亿,pct占流通市值比%}
import json, datetime, urllib.request

URL = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
       "reportName=RPTA_RZRQ_LSHJ&columns=ALL&source=WEB&client=WEB"
       "&sortColumns=DIM_DATE&sortTypes=-1&pageSize=300&pageNumber=1")

req = urllib.request.Request(URL, headers={
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Referer": "https://data.eastmoney.com/rzrq/",
})
d = json.loads(urllib.request.urlopen(req, timeout=30).read())
rows = (d.get("result") or {}).get("data") or []
if not rows:
    raise SystemExit(f"接口无数据: {str(d)[:300]}")
print("字段:", sorted(rows[0].keys()))

def num(v):
    try: return float(v)
    except Exception: return None

series = []
for r in rows:
    date = str(r.get("DIM_DATE", ""))[:10]
    rzye = num(r.get("RZYE"))            # 融资余额（元）
    lr = num(r.get("RZRQYE"))            # 两融余额（元）
    pct = None
    for k, v in r.items():               # 占流通市值比字段名不确定，按取值范围嗅探（0.5%-10%）
        if k in ("RZYE", "RZRQYE", "RQYE", "RZMRE", "RZCHE"): continue
        pv = num(v)
        if pv is not None and 0.5 < pv < 10 and any(t in k.upper() for t in ("ZB", "PERCENT", "BL", "RATIO")):
            pct = round(pv, 2); break
    if rzye:
        series.append({"d": date, "rzye": round(rzye/1e8, 1),
                       "lr": round(lr/1e8, 1) if lr else None, "pct": pct})
series.sort(key=lambda s: s["d"])
latest = series[-1]
# 防呆：融资余额量级应在 8千亿-8万亿元（亿元计 8000-80000）；坏数据比旧数据危害大
assert 8000 < latest["rzye"] < 80000, f"量级异常拒绝发布: {latest}"
out = {"updated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
       "series": series[-250:]}
json.dump(out, open("data/margin.json", "w"), ensure_ascii=False)
print("最新:", latest, "| 共", len(series), "天 | pct字段", "有" if latest["pct"] else "无（前端降级只显余额）")
