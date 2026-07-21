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
            # 成交量（日线才存）：算「近20日均成交额/昨日成交额」用。与收盘价按时间戳对齐。
            if itv == "1d":
                try:
                    vols = res["indicators"]["quote"][0]["volume"]
                    VOL_CACHE[sym] = {t: v for t, v in zip(ts, vols) if v}
                except (KeyError, IndexError):
                    pass
            return best
        except Exception as e:
            if i == retries - 1:
                print(f"  !! {sym} {rng}/{itv}: {e}", file=sys.stderr)
                return None
            time.sleep(2 * (i + 1))

VOL_CACHE = {}   # sym -> {ts: volume}，fetch_series 日线顺带填充

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

# 只取「日期,开,收」三段、起点 2023-01-01（广期所碳酸锂 2023-07 上市，覆盖完整）：
# 全字段+全历史的响应体几十KB，2026-07-20 实测连续三班被东财掐断连接（探针用小请求则次次成功）——
# 瘦身后连接稳定。要加更早上市的品种时把 beg 往前调即可。
EM_KLINE = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
            "&klt=101&fqt=0&fields1=f1,f2,f3&fields2=f51,f52,f53"
            "&beg=20230101&end=20500101&lmt=10000")

def fetch_series_em(secid, retries=3):
    """东财日K。返回 [(unix_ts, close)]，失败抛异常/返回 None（不静默降级）。

    用途：国内商品期货（Yahoo 与腾讯都不覆盖），如广期所碳酸锂主连 secid=225.lcm。
    2026-07-20 探针实测 GitHub 海外 runner 直连 push2his HTTP 200、数据完整
    （同域的 push2 快照接口在海外会 302，故只用日K，收盘价即现价）。
    klines 每行 = 日期,开,收,高,低,量,额；价格已是实际值（碳酸锂元/吨，decimal=0）。"""
    # 东财偶发 "Remote end closed connection without response"（2026-07-20 上线当天即撞到一次，
    # 碳酸锂整行掉成「获取失败」）→ 3 次重试 + 退避；带 Referer 更像正常浏览器请求。
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(EM_KLINE.format(secid=secid),
                                         headers={**UA, "Referer": "https://quote.eastmoney.com/"})
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.load(r)
            d = j.get("data") or {}
            pairs = []
            for row in d.get("klines") or []:
                f = row.split(",")
                t = int(datetime.datetime.strptime(f[0], "%Y-%m-%d")
                        .replace(tzinfo=datetime.timezone.utc).timestamp())
                c = float(f[2])
                if c > 0:
                    pairs.append((t, c))
            if pairs:
                return pairs
            last = ValueError("klines 为空")
        except Exception as e:
            last = e
        time.sleep(2 * (i + 1))
    raise last

def em_from_sentinel(secid):
    """ECS 哨站预抓的东财日K（data/lithium.json，杭州服务器国内直连，零障碍）。

    为什么要哨站：GitHub 海外 runner 直连 push2his 时好时坏——2026-07-20 首班成功、随后连续三班
    被掐断（Remote end closed connection），而同一时刻探针小请求又能通。与其赌运气，不如让国内
    服务器把数据备好，直连降级为兜底。哨站超过 10 天没更新即视为失效，不拿陈旧数据冒充新鲜。"""
    p = ROOT / "data/lithium.json"
    if not p.exists():
        return None
    try:
        rows = (json.loads(p.read_text(encoding="utf-8")).get("series") or {}).get(secid)
    except Exception as e:
        print(f"  !! 哨站文件解析失败: {e}", file=sys.stderr)
        return None
    if not rows:
        return None
    pairs = [(int(datetime.datetime.strptime(d, "%Y-%m-%d")
               .replace(tzinfo=datetime.timezone.utc).timestamp()), float(c)) for d, c in rows]
    if time.time() - pairs[-1][0] > 10 * 86400:
        print(f"  !! 哨站数据过期（最新 {rows[-1][0]}），改走直连", file=sys.stderr)
        return None
    return pairs

def fetch_history(sym, hist=None):
    """近5年日线（算涨跌幅） + 全历史月线（算历史高点），规避 Yahoo 对老股票
    range=max 时悄悄降级粒度/截断近期数据的问题。

    **没有「拿不到就偷偷换源」的兜底。** 静默兜底会把「我们写错了」伪装成「数据源不给力」，
    让真因永远查不出来。拿不到就报错，去查原因。

    唯一的例外是 `hist` 字段：**显式声明**某只标的的历史来自别处，写在 watchlist 里一眼可见，
    声明源若失败则直接报错，绝不再往下退。

    2026-07-10 逐一实测 Yahoo v8 chart（range=5y&interval=1d，日/周/月线与 max、显式起止、
    query1/query2 结果一致）返回的日线根数：

        上证指数 000001.SS  1211      科创50   000688.SS  1 ← 缺
        沪深300  000300.SS  1211      上证50   000016.SS  1 ← 缺
        深证成指 399001.SZ  1211      中证500  000905.SS  1 ← 缺
        恒生指数 ^HSI       1227      中证1000 000852.SS  1 ← 缺
        恒生国企 ^HSCE      1227      创业板指 399006.SZ  1 ← 缺
                                      恒生科技 HSTECH.HK  1 ← 缺（firstTradeDate 亦为 null）

    老牌宽基齐全，这 6 个新指数一律只给 1 根。**是 Yahoo 的覆盖缺口，不是我们的 bug**，
    故为这 6 个显式声明历史取自腾讯日K（腾讯对 A股/港股 有完整日线；美日韩台则没有，别乱用）。"""
    if hist and hist.startswith("em:"):
        secid = hist[3:]
        daily = em_from_sentinel(secid)      # 主源＝ECS 哨站文件；直连东财只作兜底
        if daily:
            print(f"  ~~ {sym} 历史取自哨站 data/lithium.json（{len(daily)} 根日K）")
            return daily, max(c for _, c in daily)
        try:
            daily = fetch_series_em(secid)
        except Exception as e:
            print(f"  !! {sym} 声明的历史源 {hist} 失败: {e}", file=sys.stderr)
            return None
        if not daily:
            print(f"  !! {sym} 声明的历史源 {hist} 返回空", file=sys.stderr)
            return None
        print(f"  ~~ {sym} 历史取自 {hist}（{len(daily)} 根日K）")
        return daily, max(c for _, c in daily)
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
    so = {}     # sym -> 总股本（sharesOutstanding，算日韩台换手率用；口径=总股本，页面已注明）
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
                sh = q.get("sharesOutstanding")
                if sh:
                    so[q["symbol"]] = sh
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
    return pe, earn, ext, fpe, mcap, so

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

def _last_price(sym):
    """取最近一个收盘价（5日日K的最后一根）。失败返回 None，不静默造数。"""
    s = fetch_series(sym, "5d", "1d")
    return s[-1][1] if s else None

def fetch_btc_mcap(cg_id, yahoo_fallback, supply_fallback):
    """比特币总市值：优先 CoinGecko 实时市值；429/失败则退回 雅虎BTC价×流通量估算。"""
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd&include_market_cap=true"
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.load(r)
        mc = j[cg_id]["usd_market_cap"]
        if mc and mc > 0:
            return mc
    except Exception as e:
        print(f"  !! BTC CoinGecko 失败，改用雅虎估算：{e}", file=sys.stderr)
    px = _last_price(yahoo_fallback)
    return px * supply_fallback if px else None

def build_market_scale():
    """生成 data/marketscale.json：全球主要股市 + 金银 + 比特币的总市值（美元）。
    - 股市：真实市值锚点 × 大盘指数/锚点指数（跟随大盘自动浮动）
    - 金银：现货价 × 地上存量
    - 比特币：CoinGecko 实时市值
    每轮流水线自动刷新，无需人工。"""
    try:
        cfg = json.loads((ROOT / "config/marketscale.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"::warning::marketscale 配置读取失败，跳过：{e}")
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.astimezone(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M") + " 北京时间"

    stocks = []
    for s in cfg.get("stocks", []):
        lvl = _last_price(s["index"])
        if lvl is None:
            print(f"  !! 市场规模 {s['key']} 指数 {s['index']} 拉取失败", file=sys.stderr)
            t = None
        else:
            t = s["cap_usd_t"] * (lvl / s["index_anchor"])   # 锚点市值×指数涨跌
        stocks.append({"key": s["key"], "flag": s["flag"], "usd_t": (round(t, 2) if t else None)})

    metals = []
    for m in cfg.get("metals", []):
        px = _last_price(m["yahoo"])
        t = (px * m["oz"] / 1e12) if px else None
        metals.append({"key": m["key"], "flag": m["flag"], "usd_t": (round(t, 2) if t else None)})

    crypto = []
    for c in cfg.get("crypto", []):
        mc = fetch_btc_mcap(c["cg_id"], c.get("yahoo_fallback"), c.get("supply_fallback", 0))
        t = (mc / 1e12) if mc else None
        crypto.append({"key": c["key"], "flag": c["flag"], "usd_t": (round(t, 2) if t else None)})

    out = {"updated": stamp, "anchor_date": cfg.get("anchor_date", ""),
           "stocks": stocks, "metals": metals, "crypto": crypto}
    (ROOT / "data/marketscale.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    line = " | ".join(f"{x['key']}{x['usd_t']}" for x in stocks + metals + crypto)
    print(f"市场规模：{line}（万亿美元）")

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

    # em: 源的标的（国内商品期货）没有 Yahoo 代码，别拿去问 Yahoo 的 v7 quote
    all_syms = [it["yahoo"] for sec in watch["sections"] for it in sec["items"]
                if not str(it.get("hist") or "").startswith("em:")]
    pe_map, earn_map, ext_map, fpe_map, mcap_map, so_map = fetch_pe_map(all_syms)

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
                             "-", "-", 0.0, "-", "-", "-", "-", "-", gmap.get(it["code"], ""), None, None, None, None, None, None, None, None, None, None])
                continue
            pairs, hist_max = fetched
            price = pairs[-1][1]
            cur = it["currency"]
            ath = max(hist_max, it.get("ath_floor") or 0)  # 兜底：配置的历史高点下限
            # ath_since：借壳/更名/反向拆股的公司，早年价格与今天的生意毫无关系
            # （KTOS 前身 Wireless Facilities 在 2000 年泡沫顶复权价 $1240、RCAT 前身
            # TimefireVR 复权价高达 $36 万，直接算出 −96%/−100% 的假回撤）。
            # 声明起算日后，历史高点只从该日之后取；日线不够长就退到现有数据全段最大值并提示。
            since = it.get("ath_since")
            if since:
                ts0 = int(datetime.datetime.strptime(since, "%Y-%m-%d")
                          .replace(tzinfo=datetime.timezone.utc).timestamp())
                vals = [c for t, c in pairs if t >= ts0]
                if vals:
                    if pairs[0][0] > ts0:
                        print(f"  ~~ {sym} ath_since={since} 早于日线起点，历史高点按现有 "
                              f"{len(pairs)} 根日线的最大值计")
                    ath = max(vals)
                else:
                    print(f"  !! {sym} ath_since={since} 之后无数据，沿用全历史高点", file=sys.stderr)
            dd = pct(price, ath)

            def ma_list():
                """五条关键均线 [MA20,MA50,MA60,MA120,MA200]，数据不足的档位为 None。
                复用已抓的5年复权日线，零额外请求（2026-07-13 主人需求：现价浮窗均线支撑）。"""
                closes = [c for _, c in pairs]
                out = []
                for n in (20, 50, 60, 120, 200):
                    if len(closes) >= n:
                        v = sum(closes[-n:]) / n
                        out.append(round(v, 2) if v >= 1 else round(v, 6))
                    else:
                        out.append(None)
                return out

            def vol_info():
                """r[21]=[avg20成交额,昨日成交额,昨日成交量,总股本]（本币元/股）。
                成交额=收盘价×成交量（日线近似，与各行情软件日口径一致）；缺任何一项该位为 None。"""
                vc = VOL_CACHE.get(sym) or {}
                amts, last_amt, last_vol = [], None, None
                for t, c in pairs[-25:]:
                    v = vc.get(t)
                    if v:
                        amts.append(c * v)
                        last_amt, last_vol = c * v, v
                avg20 = round(sum(amts[-20:]) / len(amts[-20:])) if len(amts[-20:]) >= 5 else None
                return [avg20, round(last_amt) if last_amt else None, last_vol,
                        so_map.get(sym)]

            def last5_daily():
                """r[22]=近5根日线的逐日涨跌 [[yyyymmdd,pct],...] 新→旧（2026-07-17 主人需求：三日节奏窗口）。
                bar时间戳的UTC日期即会话日期（A股01:30Z/美股13:30Z/日股00:00Z均落在本地同日）。
                前端据日期+盘中状态决定「昨日/前日」取第几个；浮窗「近5日」直接全量展示。零额外请求。"""
                out = []
                for i in range(1, 6):
                    if len(pairs) < i + 1:
                        break
                    t, c = pairs[-i]
                    prev_c = pairs[-i - 1][1]
                    d = int(datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y%m%d"))
                    out.append([d, round(pct(c, prev_c), 2)])
                return out or None

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
                         w1, fpe_map.get(sym), ma_list(), vol_info(), last5_daily()])
            print(f"  {it['code']:>10} {it['name'][:12]:<14} 现价 {price:,.2f}  回撤 {dd:.1f}%")
        sections_out.append({"sec": sec["name"], "rows": rows})

    # 完整性校验（2026-07-16 IAU 19字段坏行事故后加）：任何行长度不等于23一律拒绝发布，
    # 让 workflow 失败、线上保留旧数据——坏数据比旧数据危害大得多。（2026-07-17 r[22]近5日 22→23）
    for _s in sections_out:
        for _r in _s["rows"]:
            if len(_r) != 23:
                raise SystemExit(f"::error::行完整性校验失败 {_r[1] if len(_r)>1 else _r} 长度{len(_r)}≠23，拒绝发布")
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
    # 防呆：Yahoo v7 quote 偶发整批失败（cookie/crumb 过期、限流），earn_map 会空，
    # 直接写入就把已有财报日历清零——2026-07-21 19:00 那班实测把 113 场写成 0 场，
    # 前端财报倒计时徽标随之全灭。**坏数据比旧数据危害大**（同 IAU 坏行、fmtPct 教训），
    # 故本次结果显著少于上一版（不足三成）时保留旧文件，只打印告警。
    ev_path = ROOT / "data/events.json"
    prev_n = 0
    if ev_path.exists():
        try:
            prev_n = len(json.loads(ev_path.read_text(encoding="utf-8")).get("earnings") or [])
        except Exception:
            prev_n = 0
    if prev_n >= 10 and len(ev_rows) < prev_n * 0.3:
        print(f"  !! 财报日历本次仅 {len(ev_rows)} 场、上一版 {prev_n} 场，疑似 Yahoo v7 整批失败，"
              f"保留旧文件不覆盖", file=sys.stderr)
    else:
        ev_path.write_text(
            json.dumps({"updated": out["updated"], "earnings": ev_rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"财报日历：未来120天 {len(ev_rows)} 场")
    # ==== 市场规模（全球主要股市 + 金银 + 比特币总市值）====
    build_market_scale()
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
