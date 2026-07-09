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
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={itv}&events=div%2Csplit"

def fetch_series(sym, rng, itv, retries=3):
    url = CHART.format(sym=urllib.parse.quote(sym, safe=""), rng=rng, itv=itv)
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.load(r)
            res = j["chart"]["result"][0]
            ts = res["timestamp"]
            try:
                closes = res["indicators"]["adjclose"][0]["adjclose"]
            except (KeyError, IndexError):
                closes = res["indicators"]["quote"][0]["close"]
            pairs = [(t, c) for t, c in zip(ts, closes) if c is not None]
            if not pairs:
                raise ValueError("empty series")
            return pairs
        except Exception as e:
            if i == retries - 1:
                print(f"  !! {sym} {rng}/{itv}: {e}", file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))

def fetch_history(sym):
    """近5年日线（算涨跌幅） + 全历史月线（算历史高点），规避 Yahoo 对老股票
    range=max 时悄悄降级粒度/截断近期数据的问题。"""
    daily = fetch_series(sym, "5y", "1d")
    monthly = fetch_series(sym, "max", "1mo")
    if daily is None:
        return None
    hist_max = max(c for _, c in daily)
    if monthly:
        hist_max = max(hist_max, max(c for _, c in monthly))
    return daily, hist_max

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

def fetch_pe_map(symbols):
    """批量获取 TTM 市盈率 + 下次财报日（同一响应顺带取出，零额外请求）。
    v7 quote 接口需 cookie+crumb；失败则整体降级为空（前端显示—）。"""
    pe = {}
    earn = {}   # sym -> (unix_ts, is_estimate)
    ext = {}    # sym -> {"px","pct","st"} 美股盘前/盘后价；新鲜度＝本流水线触发频率（美股时段由 ECS 定时器每6分钟触发）
    fpe = {}    # sym -> 远期PE（亏损公司估值补位）
    import urllib.request as ur
    opener = ur.build_opener(ur.HTTPCookieProcessor())
    opener.addheaders = list(UA.items())
    try:
        opener.open("https://fc.yahoo.com", timeout=15).read(0)  # 种 cookie（返回404无妨）
    except Exception:
        pass
    try:
        crumb = opener.open("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15).read().decode()
    except Exception as e:
        print(f"::warning::PE 获取降级（crumb 失败: {e}），本次 PE 列为空")
        return pe
    syms = list(dict.fromkeys(symbols))
    for i in range(0, len(syms), 40):
        chunk = syms[i:i+40]
        url = ("https://query1.finance.yahoo.com/v7/finance/quote?symbols="
               + urllib.parse.quote(",".join(chunk)) + "&crumb=" + urllib.parse.quote(crumb))
        try:
            j = json.load(opener.open(url, timeout=20))
            for q in j.get("quoteResponse", {}).get("result", []):
                v = q.get("trailingPE")
                if v is None and q.get("epsTrailingTwelveMonths") and q.get("regularMarketPrice"):
                    eps = q["epsTrailingTwelveMonths"]
                    if eps > 0:
                        v = q["regularMarketPrice"] / eps
                if v is not None and 0 < v < 100000:
                    pe[q["symbol"]] = round(v, 1)
                fv = q.get("forwardPE")
                if fv is not None and 0 < fv < 100000:
                    fpe[q["symbol"]] = round(fv, 1)
                st = q.get("marketState", "")
                if st.startswith("PRE") and q.get("preMarketPrice"):
                    ext[q["symbol"]] = {"px": q["preMarketPrice"], "pct": q.get("preMarketChangePercent"), "st": "盘前"}
                elif st.startswith("POST") and q.get("postMarketPrice"):
                    ext[q["symbol"]] = {"px": q["postMarketPrice"], "pct": q.get("postMarketChangePercent"), "st": "盘后"}
                ets = q.get("earningsTimestamp") or q.get("earningsTimestampStart")
                if ets:
                    est = bool(q.get("isEarningsDateEstimate")) or (
                        q.get("earningsTimestampStart") and q.get("earningsTimestampEnd")
                        and q["earningsTimestampStart"] != q["earningsTimestampEnd"])
                    earn[q["symbol"]] = (ets, bool(est))
        except Exception as e:
            print(f"  !! PE 批次 {i//40} 失败: {e}", file=sys.stderr)
        time.sleep(0.5)
    print(f"PE 覆盖 {len(pe)}/{len(syms)} 个代码，财报日 {len(earn)} 个，盘前后价 {len(ext)} 个")
    return pe, earn, ext, fpe

def _num(v):
    if v >= 10000:
        return f"{v:,.0f}"
    if v >= 100:
        return f"{v:,.1f}"
    return f"{v:.2f}" if v >= 1 else f"{v:.3f}"

def pos_52w(pairs, ts_1y, cur):
    """现价在近52周高低点区间的位置（0-100）＋区间字符串「低–高」"""
    win = [c for t, c in pairs if t >= ts_1y] or [c for _, c in pairs]
    hi, lo = max(win), min(win)
    rng = f"{cur}{_num(lo)}–{_num(hi)}"
    if hi == lo:
        return 50.0, rng
    return round((pairs[-1][1] - lo) / (hi - lo) * 100, 0), rng

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
    ts_1w = int((now - datetime.timedelta(days=7)).timestamp())
    ts_1m = int((now - datetime.timedelta(days=30)).timestamp())
    ts_3m = int((now - datetime.timedelta(days=91)).timestamp())
    ts_6m = int((now - datetime.timedelta(days=182)).timestamp())
    ts_1y = int((now - datetime.timedelta(days=365)).timestamp())
    ts_ytd = int(datetime.datetime(now.year, 1, 1, tzinfo=datetime.timezone.utc).timestamp())  # 基准=上年最后一个收盘

    all_syms = [it["yahoo"] for sec in watch["sections"] for it in sec["items"]]
    pe_map, earn_map, ext_map, fpe_map = fetch_pe_map(all_syms)

    cache = {}
    sections_out = []
    for sec in watch["sections"]:
        rows = []
        gmap = {}
        for g in sec.get("groups", []):
            for c in g["codes"]:
                gmap[c] = g["name"]
        for it in sec["items"]:
            sym = it["yahoo"]
            if sym not in cache:
                cache[sym] = fetch_history(sym)
                time.sleep(0.4)  # 温和限速
            fetched = cache[sym]
            if fetched is None:
                rows.append([it["name"], it["code"], it["market"], "获取失败",
                             "-", "-", 0.0, "-", "-", "-", "-", "-", gmap.get(it["code"], ""), None, None, None, None, None, None, None])
                continue
            pairs, hist_max = fetched
            price = pairs[-1][1]
            cur = it["currency"]
            ath = max(hist_max, it.get("ath_floor") or 0)  # 兜底：配置的历史高点下限
            dd = pct(price, ath)

            def window(ts_base, label_ipo):
                base = price_at(pairs, ts_base)
                if base is None:  # 上市不足该窗口
                    ipo = pairs[0][1]
                    v = pct(price, ipo)
                    return f"上市后 {'+' if v >= 0 else ''}{v:.1f}%"
                return round(pct(price, base), 1)

            w1 = window(ts_1w, "1w")
            m1 = window(ts_1m, "1m")
            m3 = window(ts_3m, "3m")
            m6 = window(ts_6m, "6m")
            ytd = window(ts_ytd, "ytd")
            y1 = window(ts_1y, "1y")

            rows.append([it["name"], it["code"], it["market"],
                         fmt_mcap(it, price),
                         fmt_price(cur, ath), fmt_price(cur, price),
                         round(dd, 1), m1, m3, m6, ytd, y1, gmap.get(it["code"], ""),
                         pe_map.get(sym), *pos_52w(pairs, ts_1y, cur),
                         round(pct(pairs[-1][1], pairs[-2][1]), 2) if len(pairs) >= 2 else None,
                         ext_map.get(sym) if it["market"].startswith("美股") else None,
                         w1, fpe_map.get(sym)])
            print(f"  {it['code']:>10} {it['name'][:12]:<14} 现价 {price:,.2f}  回撤 {dd:.1f}%")
        sections_out.append({"sec": sec["name"], "rows": rows})

    out = {"updated": now.astimezone(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M") + " 北京时间",
           "sections": sections_out}
    (ROOT / "data/quotes.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    # ==== 财报日历（自动层）====
    seen_ev, ev_rows = set(), []
    for sec in watch["sections"]:
        for it in sec["items"]:
            sym = it["yahoo"]
            if it["code"] in seen_ev or sym not in earn_map:
                continue
            seen_ev.add(it["code"])
            ets, est = earn_map[sym]
            if not (ts_now - 86400 <= ets <= ts_now + 120 * 86400):
                continue   # 只留未来120天（含今天）
            d = datetime.datetime.fromtimestamp(ets, datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")
            ev_rows.append({"d": d, "code": it["code"], "name": it["name"], "est": est})
    ev_rows.sort(key=lambda x: x["d"])
    (ROOT / "data/events.json").write_text(
        json.dumps({"updated": out["updated"], "earnings": ev_rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"财报日历：未来120天 {len(ev_rows)} 场")
    rows_all = [r for s in sections_out for r in s["rows"]]
    fails = sum(1 for r in rows_all if r[3] == "获取失败")
    flat_1m = sum(1 for r in rows_all if r[7] == 0.0)
    print(f"\n完成：{len(rows_all)} 行，失败 {fails} 行")
    # ==== 数据质检 ====
    if flat_1m > len(rows_all) * 0.1:
        print(f"::warning::质检警告：{flat_1m} 行「近1个月」恰好为 0.0%，疑似行情序列缺失近期数据")
    stale = [sym2 for sym2, v in cache.items()
             if v and (ts_now - v[0][-1][0]) > 7 * 86400]
    if stale:
        print(f"::warning::质检警告：{len(stale)} 个代码行情超过7天未更新: {', '.join(stale[:10])}")
    if fails > 0:
        print(f"::warning::有 {fails} 行获取失败，请检查代码配置")

if __name__ == "__main__":
    main()
