#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日行情更新脚本：读取 config/watchlist.json，从 Yahoo Finance 拉取全量历史，
计算 历史高点 / 高点至今跌幅 / 近1个月 / 年初至今 / 近1年，写入 data/quotes.json。

- 所有市场（美/A/港/日/韩/商品/加密）统一走 Yahoo chart API，无需鉴权
- 市值为估算值：按 watchlist 中的基准市值随最新价同比例滚动
- 上市不足一年/不足年初的标的，显示"上市后 +x%"口径
"""
import json, time, datetime, urllib.request, urllib.parse, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=max&interval=1d"

def fetch_history(sym, retries=3):
    url = CHART.format(sym=urllib.parse.quote(sym, safe=""))
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.load(r)
            res = j["chart"]["result"][0]
            ts = res["timestamp"]
            closes = res["indicators"]["quote"][0]["close"]
            pairs = [(t, c) for t, c in zip(ts, closes) if c is not None]
            if not pairs:
                raise ValueError("empty series")
            return pairs
        except Exception as e:
            if i == retries - 1:
                print(f"  !! {sym}: {e}", file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))

def pct(cur, base):
    return (cur / base - 1) * 100

def price_at(pairs, target_ts):
    """target_ts 之前最近一个交易日的收盘价；若上市晚于 target_ts 返回 None"""
    if pairs[0][0] > target_ts:
        return None
    best = None
    for t, c in pairs:
        if t <= target_ts:
            best = c
        else:
            break
    return best

def fmt_price(cur, v):
    if v >= 10000:
        return f"{cur}{v:,.0f}"
    if v >= 100:
        return f"{cur}{v:,.2f}"
    return f"{cur}{v:.2f}" if v >= 1 else f"{cur}{v:.3f}"

def fmt_mcap(item, price):
    mb = item.get("mcap_base")
    if not mb:
        return "不适用"
    scaled = mb["yi"] * price / item["mcap_base_price"]
    prefix = mb["prefix"]
    if scaled >= 10000:
        return f"{prefix}{scaled/10000:.2f}万亿"
    return f"{prefix}{scaled:.1f}亿"

def main():
    watch = json.loads((ROOT / "config/watchlist.json").read_text(encoding="utf-8"))
    now = datetime.datetime.now(datetime.timezone.utc)
    ts_now = int(now.timestamp())
    ts_1m = int((now - datetime.timedelta(days=30)).timestamp())
    ts_1y = int((now - datetime.timedelta(days=365)).timestamp())
    ts_ytd = int(datetime.datetime(now.year - 1, 12, 31, tzinfo=datetime.timezone.utc).timestamp())

    cache = {}
    sections_out = []
    for sec in watch["sections"]:
        rows = []
        for it in sec["items"]:
            sym = it["yahoo"]
            if sym not in cache:
                cache[sym] = fetch_history(sym)
                time.sleep(0.6)  # 温和限速
            pairs = cache[sym]
            if pairs is None:
                rows.append([it["name"], it["code"], it["market"], "获取失败",
                             "-", "-", 0.0, "-", "-", "-"])
                continue
            price = pairs[-1][1]
            cur = it["currency"]
            ath = max(c for _, c in pairs)
            ath = max(ath, it.get("ath_floor") or 0)  # 行情源历史不全时兜底
            dd = pct(price, ath)

            def window(ts_base, label_ipo):
                base = price_at(pairs, ts_base)
                if base is None:  # 上市不足该窗口
                    ipo = pairs[0][1]
                    v = pct(price, ipo)
                    return f"上市后 {'+' if v >= 0 else ''}{v:.1f}%"
                return round(pct(price, base), 1)

            m1 = window(ts_1m, "1m")
            ytd = window(ts_ytd, "ytd")
            y1 = window(ts_1y, "1y")

            rows.append([it["name"], it["code"], it["market"],
                         fmt_mcap(it, price),
                         fmt_price(cur, ath), fmt_price(cur, price),
                         round(dd, 1), m1, ytd, y1])
            print(f"  {it['code']:>10} {it['name'][:12]:<14} 现价 {price:,.2f}  回撤 {dd:.1f}%")
        sections_out.append({"sec": sec["name"], "rows": rows})

    out = {"updated": now.astimezone(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M") + " 北京时间",
           "sections": sections_out}
    (ROOT / "data/quotes.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    fails = sum(1 for s in sections_out for r in s["rows"] if r[3] == "获取失败")
    print(f"\n完成：{sum(len(s['rows']) for s in sections_out)} 行，失败 {fails} 行")

if __name__ == "__main__":
    main()
