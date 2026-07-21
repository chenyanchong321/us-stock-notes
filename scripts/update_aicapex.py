#!/usr/bin/env python3
# AI 资金底图 · 第②层「谁在花」：美股大厂资本开支季度全历史（GitHub Actions runner，2026-07-21）
#
# 源 = SEC XBRL companyconcept API（官方结构化财报数据，免费无需 key，只要求 User-Agent 带联系方式）
# 沙箱连不上 sec.gov（实测 curl 000），必须在 runner 上跑，同 update_margin.py 的路数。
#
# 【核心难点：现金流量表在 10-Q 里是「年初至今累计」，不是单季】
#   例：Alphabet 的 Q2 报表给的是 1/1–6/30 半年数，要减去 Q1 才是真正的 Q2。
#   解法 = 同一 start 日期的累计序列逐项差分（见 to_quarters）。这是本脚本唯一的技术含量，别改坏。
#
# 【口径声明（面板上必须原样公示）】
#   capex = 现金流量表「购置不动产、厂房及设备支付的现金」，不含融资租赁、不含并购。
#   微软/甲骨文/英伟达财年与日历年不同，本脚本一律按「报告期结束日」归入自然季度，跨公司才可比。
#   融资租赁单列（微软、Meta 大量使用，只看 capex 会显著低估其真实投入）。
import json, time, datetime, urllib.request, urllib.error

UA = "yancong us-stock-notes research chimneycyc@gmail.com"

# 花钱方为主，AAPL 作为「没参与军备竞赛」的对照组（这条对比线本身就是内容）
COMPANIES = [
    ("GOOGL", "Alphabet",   "0001652044", "美股"),
    ("MSFT",  "微软",        "0000789019", "美股"),
    ("AMZN",  "亚马逊",      "0001018724", "美股"),
    ("META",  "Meta",       "0001326801", "美股"),
    ("ORCL",  "甲骨文",      "0001341439", "美股"),
    ("NVDA",  "英伟达",      "0001045810", "美股"),
    ("TSLA",  "特斯拉",      "0001318605", "美股"),
    ("AAPL",  "苹果（对照）", "0000320193", "美股"),
]

# 【2026-07-21 首跑教训】各家用的 XBRL 标签不一致，且会中途更换：
#   亚马逊 PaymentsToAcquirePropertyPlantAndEquipment 只到 2017Q1、英伟达只到 2020Q3，都已废弃改用别的标签。
#   所以绝不能"按优先级取第一个有数据的"——那会死抱废弃标签。
#   正解 = 拉 companyfacts 一次拿全所有概念，正则筛出候选，自动挑「最新且最全」的那个，再用其余补缺口。
import re
CAPEX_RE = re.compile(r"^(Payments|Purchase)[A-Za-z]*(PropertyPlantAndEquipment|ProductiveAssets|PropertyAndEquipment)")
LEASE_RE = re.compile(r"FinanceLease.*(ObtainedInExchange|RightOfUseAsset)|RightOfUseAssetObtainedInExchangeForFinanceLease")
# 排除退款/处置/出售类科目（它们是现金流入，混进来会把 capex 算小）
EXCLUDE_RE = re.compile(r"Proceeds|Disposal|Sale|Refund|Receivable|Held", re.I)


def fetch_facts(cik):
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    raw = urllib.request.urlopen(req, timeout=90).read()
    import gzip, io
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


def to_quarters(entries):
    """把 SEC 的混合期间条目（单季/半年/九月/全年累计）还原成单季值。
    返回 {"2026Q1": 亿美元, ...}"""
    # 去重：同一 (start,end) 可能在多份报表里重复出现，取最新报告的值（fy 大者优先）
    best = {}
    for e in entries:
        s, en, v = e.get("start"), e.get("end"), e.get("val")
        if not (s and en and v is not None):
            continue
        if e.get("form") not in ("10-Q", "10-K", "10-K/A", "10-Q/A"):
            continue
        k = (s, en)
        prev = best.get(k)
        if prev is None or (e.get("fy") or 0, e.get("fp") or "") >= (prev.get("fy") or 0, prev.get("fp") or ""):
            best[k] = e

    # 按 start 分组，同一起点的累计序列逐项差分 → 单季
    by_start = {}
    for (s, en), e in best.items():
        by_start.setdefault(s, []).append((en, float(e["val"])))

    singles = {}   # (period_start, period_end) -> val
    for s, lst in by_start.items():
        lst.sort()
        prev_end, prev_val = None, 0.0
        for en, v in lst:
            span = (datetime.date.fromisoformat(en) - datetime.date.fromisoformat(prev_end or s)).days
            if 60 <= span <= 110:                      # 差出来是一个正常季度长度才认
                singles[(prev_end or s, en)] = v - prev_val
            prev_end, prev_val = en, v

    # 归入自然季度（按报告期结束日），跨公司才可比
    out = {}
    for (_, en), v in singles.items():
        d = datetime.date.fromisoformat(en)
        # 结束日落在月初的按上个月算（如 4/1 结束视为 Q1）
        m = d.month if d.day > 5 else (d.month - 1 or 12)
        y = d.year if not (d.day <= 5 and d.month == 1) else d.year - 1
        q = f"{y}Q{(m - 1) // 3 + 1}"
        out[q] = round(v / 1e8, 1)                     # 亿美元
    return dict(sorted(out.items()))


def series_for(facts, pattern):
    """扫描全部 us-gaap 概念，选出最新且最全的候选做主序列，其余候选补缺口。"""
    cands = []
    for tag, node in (facts.get("facts", {}).get("us-gaap") or {}).items():
        if not pattern.search(tag) or EXCLUDE_RE.search(tag):
            continue
        ents = (node.get("units") or {}).get("USD") or []
        q = to_quarters(ents)
        if q:
            cands.append((max(q), len(q), tag, q))       # 按(最新季度, 季度数)择优
    if not cands:
        return {}, None
    cands.sort(reverse=True)
    _, _, best_tag, merged = cands[0]
    merged = dict(merged)
    for _, _, _, q in cands[1:]:                         # 其余候选只补主序列没有的季度
        for k, v in q.items():
            merged.setdefault(k, v)
    used = best_tag if len(cands) == 1 else f"{best_tag}(+{len(cands)-1})"
    return dict(sorted(merged.items())), used


companies, diag = {}, []
for tic, name, cik, mkt in COMPANIES:
    facts = fetch_facts(cik)
    time.sleep(0.3)                                      # SEC 限速 10 req/s，留足余量
    capex, ctag = series_for(facts, CAPEX_RE)
    lease, ltag = series_for(facts, LEASE_RE)
    if not capex:
        diag.append(f"{tic}: capex 抓取失败（所有 tag 均无数据）")
        continue
    companies[tic] = {"name": name, "cik": cik, "mkt": mkt, "tag": ctag,
                      "capex": capex, "lease": lease}
    last = list(capex.items())[-1]
    diag.append(f"{tic}: {len(capex)}季 最新 {last[0]}={last[1]}亿美元 tag={ctag} 租赁{len(lease)}季")
    print(diag[-1])

# 防呆（坏数据比旧数据危害大，同 IAU 事故教训）：
# 头部四家最近一个完整季度 capex 应在 50–1500 亿美元区间，出界即拒绝发布
for tic in ("GOOGL", "MSFT", "AMZN", "META"):
    c = companies.get(tic, {}).get("capex") or {}
    if not c:
        raise SystemExit(f"量级校验失败：{tic} 无数据")
    vals = [v for v in list(c.values())[-4:]]
    if not any(50 <= v <= 1500 for v in vals):
        raise SystemExit(f"量级异常拒绝发布：{tic} 近四季 {vals}")

out = {"updated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
       "unit": "亿美元",
       "note": "capex=现金流量表购置不动产厂房设备支付的现金，不含融资租赁与并购；按报告期结束日归入自然季度",
       "src": "SEC XBRL companyconcept API",
       "companies": companies, "diag": diag}
json.dump(out, open("data/aicapex.json", "w"), ensure_ascii=False)
print("\n写入 data/aicapex.json：", len(companies), "家")
