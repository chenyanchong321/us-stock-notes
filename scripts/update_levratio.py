#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存储杠杆率（2026-07-15，源自 Leto Bao 思路，口径为本站自建）：
每标的 杠杆ETF/ETN 成交额(美元) ÷ 正股(含ADR/GDR)成交额(美元)，按交易日序列输出。

- 数据源：Yahoo chart API 日线（close×volume=当日成交额近似；KRW/JPY 经每日汇率折美元）。
- 产品清单 config/levratio.json 人工维护；抓不到的代码记进 missing，页面上透明展示。
- 输出 data/levratio.json：每标的最近60个交易日 ratio 序列 + 最新多空拆分。
"""
import json, time, urllib.request, datetime

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
CCY_FX = {"KRW": "KRW=X", "JPY": "JPY=X", "HKD": "HKD=X", "GBp": "GBPUSD=X"}


def chart(sym, rng="3mo"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d"
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            j = json.loads(urllib.request.urlopen(req, timeout=20).read())
            r = j["chart"]["result"][0]
            q = r["indicators"]["quote"][0]
            ccy = r["meta"].get("currency") or "USD"
            out = {}
            for ts, c, v in zip(r["timestamp"], q["close"], q["volume"]):
                if c and v:
                    d = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                    out[d] = c * v
            return ccy, out
        except Exception:
            time.sleep(2 + i * 2)
    return None, None


def main():
    cfg = json.load(open("config/levratio.json", encoding="utf-8"))

    # 汇率日序列（折美元）
    fx = {"USD": {}}
    for ccy, sym in CCY_FX.items():
        _, s = chart(sym)
        fx[ccy] = s or {}

    def usd(ccy, day, val):
        if ccy in (None, "USD"):
            return val
        if ccy == "GBp":  # 伦敦便士报价
            r = fx["GBp"].get(day) or (list(fx["GBp"].values()) or [0])[-1]
            # GBPUSD=X 的 close×volume 无意义，这里仅要 close：改存 close 见下
            return None
        series = fx.get(ccy, {})
        if not series:
            return None
        rate = series.get(day)
        if rate is None:
            days = sorted(series)
            prior = [x for x in days if x <= day]
            rate = series[prior[-1]] if prior else series[days[0]]
        return val / rate  # KRW=X 等为「1美元=多少本币」

    # 注意：汇率序列这里存的是 close 而非 close×volume —— 单独抓
    for ccy, sym in CCY_FX.items():
        for i in range(3):
            try:
                req = urllib.request.Request(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d",
                    headers=UA)
                r = json.loads(urllib.request.urlopen(req, timeout=20).read())["chart"]["result"][0]
                q = r["indicators"]["quote"][0]
                fx[ccy] = {datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"): c
                           for ts, c in zip(r["timestamp"], q["close"]) if c}
                break
            except Exception:
                time.sleep(2 + i * 2)

    cache = {}

    def dollars(sym):
        if sym not in cache:
            ccy, s = chart(sym)
            if s is None:
                cache[sym] = None
            else:
                out = {}
                for d, val in s.items():
                    u = usd(ccy, d, val)
                    if u:
                        out[d] = u
                cache[sym] = out
        return cache[sym]

    names_out, missing = [], []
    for n in cfg["names"]:
        und, lev_long, lev_short = {}, {}, {}
        for s in n["und"]:
            ser = dollars(s)
            if ser is None:
                missing.append(s)
                continue
            for d, v in ser.items():
                und[d] = und.get(d, 0) + v
        for leg in n["lev"]:
            ser = dollars(leg["t"])
            if ser is None:
                missing.append(leg["t"])
                continue
            tgt = lev_long if leg["x"] > 0 else lev_short
            for d, v in ser.items():
                tgt[d] = tgt.get(d, 0) + v
        days = sorted(und)[-60:]
        hist = []
        for d in days:
            lv = lev_long.get(d, 0) + lev_short.get(d, 0)
            if und.get(d):
                hist.append([d, round(lv / und[d], 4)])
        last = hist[-1] if hist else None
        names_out.append({
            "key": n["key"], "label": n["label"],
            "hist": hist,
            "ratio": last[1] if last else None,
            "d1": round(hist[-1][1] - hist[-2][1], 4) if len(hist) >= 2 else None,
            "longM": round(lev_long.get(last[0], 0) / 1e6) if last else 0,
            "shortM": round(lev_short.get(last[0], 0) / 1e6) if last else 0,
            "undB": round(und.get(last[0], 0) / 1e9, 2) if last else 0,
            "nlev": len(n["lev"]),
        })

    out = {"updated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
           "missing": sorted(set(missing)), "names": names_out}
    json.dump(out, open("data/levratio.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("levratio:", [(x["key"], x["ratio"]) for x in names_out], "missing:", out["missing"])


if __name__ == "__main__":
    main()
