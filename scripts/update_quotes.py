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
            # 不能盲目优先 adjclose：指数没有复权概念，Yahoo 会返回一个几乎全是 null 的 adjclose 数组，
            # 于是 pairs 只剩最后一个非空点 → 近1月/近1年全变「上市后 +0.0%」、52周区间塌成一个点。
            # 正确做法：两个序列都取出来，谁的非空点多就用谁。
            cands = []
            try:
                cands.append(res["indicators"]["adjclose"][0]["adjclose"])
            except (KeyError, IndexError):
                pass
            try:
                cands.append(res["indicators"]["quote"][0]["close"])
            except (KeyError, IndexError):
                pass
            best = []
            for closes in cands:
                if not closes:
                    continue
                p = [(t, c) for t, c in zip(ts, closes) if c is not None]
                if len(p) > len(best):
                    best = p
            if not best:
                raise ValueError("empty series")
            return best
        except Exception as e:
            if i == retries - 1:
                print(f"  !! {sym} {rng}/{itv}: {e}", file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))

TX_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,1800,qfq"

def fetch_series_tencent(tx_code):
    """腾讯日K。返回 [(unix_ts, close)]，失败返回 None（不静默降级）。"""
    req = urllib.request.Request(TX_KLINE.format(code=tx_code), headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.load(r)
    d = j["data"][tx_code]
    arr = d.get("qfqday") or d.get("day") or []
    pairs = []
    for row in arr:                       # row = [日期, 开, 收, 高, 低, 量]
        t = int(datetime.datetime.strptime(row[0], "%Y-%m-%d")
                .replace(tzinfo=datetime.timezone.utc).timestamp())
        c = float(row[2])
        if c > 0:
            pairs.append((t, c))
    return pairs or None

def fetch_history(sym, hist=None):
    """近5年日线（算涨跌幅） + 全历史月线（算历史高点），规避 Yahoo 对老股票
    range=max 时悄悄降级粒度/截断近期数据的问题。

    **没有「拿不到就偷偷换源」的兜底。** 历史上两次「Yahoo 拿不到」都是我们自己的错：
    一次盲目优先 adjclose（指数的 adjclose 几乎全 null，只剩 1 个点），
    一次代码写错（^HSTECH 其实是 HSTECH.HK）。静默兜底会把「我们写错了」伪装成
    「数据源不给力」，让真因永远查不出来——科创50 差一点就这样被掩盖。

    唯一的例外是 `hist` 字段：**显式声明**某只标的的历史来自别处，写在 watchlist 里一眼可见。
    目前仅 HSTECH（恒生科技指数）——已穷举验证 Yahoo 对它的日/周/月线、max/显式起止、
    query1/query2 全部只返回 1 个点，firstTradeDate 为 null，而 ^HSI / ^HSCE / 3033.HK 均正常。
    声明源若失败则直接报错，绝不再往下退。"""
    if hist and hist.startswith("tx:"):
        tx_code = hist[3:]
        try:
            daily = fetch_series_tencent(tx_code)
        except Exception as e:
            print(f"  !! {sym} 声明的历史源 {hist} 失败: {e}", file=sys.stderr)
            return None
        if not daily:
            print(f"  !! {sym} 声明的历史源 {hist} 返回空", file=sys.stderr)
            return None
        print(f"  ~~ {sym} 历史取自 {hist}（{len(daily)} 根日K）")
        return daily, max(c for _, c in daily)

    daily = fetch_series(sym, "5y", "1d")
    monthly = fetch_series(sym, "max", "1mo")
    if daily is None:
        return None
    if len(daily) < 30:
        print(f"  !! {sym} 日线仅 {len(daily)} 根，疑似代码或字段有误，请排查", file=sys.stderr)
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

def tiny(v):
    """极小价格（PEPE ≈ 0.0000027）：{:.3f} 会抹成 0.000，{:.3g} 会变成科学计数 2.71e-06。
    这里补足小数位再去掉尾零，得到 0.00000271。"""
    return f"{v:.12f}".rstrip("0").rstrip(".") or "0"

def fmt_price(cur, v):
    if v >= 10000:
        return f"{cur}{v:,.0f}"
    if v >= 100:
        return f"{cur}{v:,.2f}"
    if v >= 1:
        return f"{cur}{v:.2f}"
    if v >= 0.01:
        return f"{cur}{v:.4f}"
    return f"{cur}{tiny(v)}"

def fetch_pe_map(symbols):
    """批量获取 TTM 市盈率 + 下次财报日（同一响应顺带取出，零额外请求）。
    v7 quote 接口需 cookie+crumb；失败则整体降级为空（前端显示—）。"""
    pe = {}
    earn = {}   # sym -> (unix_ts, is_estimate)
    ext = {}    # sym -> {"px","pct","st"} 美股盘前/盘后价；新鲜度＝本流水线触发频率（美股时段由 ECS 定时器每6分钟触发）
    fpe = {}    # sym -> 远期PE（亏损公司估值补位）
    mcap = {}   # sym -> 真实市值（美元）。稳定币价格恒为1，市值只随发行量变化，锚点推导会把它冻死，必须取真值
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
                mc = q.get("marketCap")
                if mc:
                    mcap[q["symbol"]] = mc
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
    print(f"PE 覆盖 {len(pe)}/{len(syms)} 个代码，财报日 {len(earn)} 个，盘前后价 {len(ext)} 个，真实市值 {len(mcap)} 个")
    return pe, earn, ext, fpe, mcap

def _num(v):
    if v >= 10000:
        return f"{v:,.0f}"
    if v >= 100:
        return f"{v:,.1f}"
    if v >= 1:
        return f"{v:.2f}"
    if v >= 0.01:
        return f"{v:.4f}"
    return tiny(v)   # 同 fmt_price：否则 52周区间会变成「$0.000–$0.000」

def pos_52w(pairs, ts_1y, cur):
    """现价在近52周高低点区间的位置（0-100）＋区间字符串「低–高」"""
    win = [c for t, c in pairs if t >= ts_1y] or [c for _, c in pairs]
    hi, lo = max(win), min(win)
    rng = f"{cur}{_num(lo)}–{_num(hi)}"
    if hi == lo:
        return 50.0, rng
    return round((pairs[-1][1] - lo) / (hi - lo) * 100, 0), rng

def fmt_mcap(item, price, live_mcap=None):
    mb = item.get("mcap_base")
    if not mb:
        return "不适用"
    prefix = mb["prefix"]
    # mcap_live：直接用交易所/行情源给的真实市值。稳定币价格恒为 1，市值只随发行量变，
    # 锚点推导（市值 ∝ 现价）会把它永远冻在锚定当天的数字上，所以必须取真值。
    if item.get("mcap_live") and live_mcap:
        scaled = live_mcap / 1e8            # 美元 → 亿
        if scaled >= 10000:
            return f"{prefix}{scaled/10000:.2f}万亿"
        return f"{prefix}{scaled:.1f}亿"
    scaled = mb["yi"] * price / item["mcap_base_price"]
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
    pe_map, earn_map, ext_map, fpe_map, mcap_map = fetch_pe_map(all_syms)

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
                cache[sym] = fetch_history(sym, it.get("hist"))   # hist＝显式声明的历史源，仅个别标的
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
                         fmt_mcap(it, price, mcap_map.get(sym)),
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
