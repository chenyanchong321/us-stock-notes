#!/bin/bash
# ECS A股市值哨站一键安装（在ECS上执行）。生成自 交接给ECS_A股市值哨站.md
set -e
cat > /root/us-stock-cnmcap.py <<'PYEOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A股总市值哨站：抓沪深两所官方股票总市值（亿元），经 GitHub API 写回 data/cn_mcap.json。"""
import json, base64, datetime, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
REPO, FPATH = "chenyanchong321/us-stock-notes", "data/cn_mcap.json"
TOKEN = open("/root/.gh_token").read().strip()

def get(url, headers=None, data=None, method=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})}, data=data, method=method)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")

def sse_total():
    q = urllib.parse.urlencode({"sqlId": "COMMON_SSE_SJ_GPSJ_GPSJZM_TJSJ_L",
                                "PRODUCT_NAME": "股票,主板,科创板", "type": "inParams"})
    j = json.loads(get(f"http://query.sse.com.cn/commonQuery.do?{q}",
                       {"Referer": "http://www.sse.com.cn/"}))
    row = [r for r in j["result"] if str(r.get("PRODUCT_NAME")) == "股票"][0]
    for k in ("TOTAL_VALUE", "MARKET_VALUE", "TOTAL_MARKET_VALUE"):
        if row.get(k):
            return float(str(row[k]).replace(",", ""))
    raise RuntimeError(f"SSE字段变更: {row}")

def szse_total():
    for back in range(0, 7):
        d = (datetime.date.today() - datetime.timedelta(days=back)).isoformat()
        j = json.loads(get("http://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON"
                           f"&CATALOGID=1803_sczm&TABKEY=tab1&txtQueryDate={d}",
                           {"Referer": "http://www.szse.cn/"}))
        for tab in (j if isinstance(j, list) else [j]):
            for r in (tab.get("data") or []):
                vals = list(r.values())
                if any(str(v).strip() == "股票" for v in vals):
                    nums = []
                    for v in vals:
                        try:
                            nums.append(float(str(v).replace(",", "")))
                        except (ValueError, TypeError):
                            pass
                    nums = [n for n in nums if 1.5e5 < n < 7e5]  # 总市值量级：15万~70万亿元
                    if nums:
                        return max(nums), d
    raise RuntimeError("SZSE 7日内无数据")

def push(payload):
    api = f"https://api.github.com/repos/{REPO}/contents/{FPATH}"
    hdr = {"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json"}
    sha = None
    try:
        sha = json.loads(get(api, hdr))["sha"]
    except Exception:
        pass
    body = {"message": "chore: ECS哨站更新A股总市值 " + payload["date"],
            "content": base64.b64encode(
                json.dumps(payload, ensure_ascii=False, indent=1).encode()).decode()}
    if sha:
        body["sha"] = sha
    get(api, {**hdr, "Content-Type": "application/json"},
        data=json.dumps(body).encode(), method="PUT")

sse = sse_total()
szse, d = szse_total()
out = {"updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "date": d,
       "sse_yi": round(sse, 1), "szse_yi": round(szse, 1), "total_yi": round(sse + szse, 1),
       "_说明": "沪深两所官方股票总市值（亿元人民币），阿里云ECS哨站每周一/四自动抓取写回"}
push(out)
print(out["updated"], "A股总市值", out["total_yi"], "亿元 ✓")
PYEOF
chmod 700 /root/us-stock-cnmcap.py
(crontab -l 2>/dev/null | grep -v us-stock-cnmcap; echo "7 18 * * 1,4 /usr/bin/python3 /root/us-stock-cnmcap.py >> /root/us-stock-cnmcap.log 2>&1") | crontab -
python3 /root/us-stock-cnmcap.py
