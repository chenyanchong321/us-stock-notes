#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stockauth：观察位会员服务（跑在 ECS 上，nginx /api/ 反代到本机 8600 端口）。

设计原则（2026-07-15 主人定稿）：
- 纯标准库零依赖；数据（auth.db / points.json）只存 ECS 本地，绝不进公开仓库。
- 注册 = 邀请码 + 用户名 + 密码，一码一号；不收手机/邮箱。忘密码走 manage.py resetpw。
- 游客只能拿到 /api/public（哪些代码设了点位，用于画★和锁）；真实点位数字只在
  /api/points 里，必须带有效登录 token —— 脱敏在服务端，数字不出服务器。
"""
import json, os, re, sqlite3, hashlib, secrets, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("STOCKAUTH_DB", os.path.join(BASE, "auth.db"))
POINTS = os.environ.get("STOCKAUTH_POINTS", os.path.join(BASE, "points.json"))
MEMBER = os.environ.get("STOCKAUTH_MEMBER", os.path.join(BASE, "member"))  # 私有仓库克隆
REPORTS_JSON = os.path.join(MEMBER, "reports.json")
REPORTS_DIR = os.path.join(MEMBER, "reports")
KEDU_POINTS = os.environ.get("STOCKAUTH_KEDU_POINTS", os.path.join(MEMBER, "kedu_points.json"))
LIVE_QUOTES = os.environ.get("STOCKAUTH_LIVE_QUOTES", "/var/www/us-stock/data/live.json")
STATIC_QUOTES = os.environ.get("STOCKAUTH_STATIC_QUOTES", "/var/www/us-stock/data/quotes.json")
LIVE_MAX_AGE_SECONDS = int(os.environ.get("STOCKAUTH_LIVE_MAX_AGE", "600"))
KEDU_ENABLED = os.environ.get("KEDU_DECISION_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
KEDU_TABLE_ENABLED = os.environ.get("KEDU_POINTS_TABLE_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
PORT = 8600
ALLOW_ORIGINS = {"https://stock.ziyuanai.top", "https://www.ziyuanai.top",
                 "https://chenyanchong321.github.io", "https://lab.ziyuanai.top"}
TOKEN_DAYS = 400          # 登录有效期：够长，熟人产品不折腾
MAX_FAILS_PER_HOUR = 30   # 单IP每小时最多失败次数（防撞库）
VALID_PERMISSIONS = {"stock_member", "kedu_points"}


def db():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY, username TEXT UNIQUE, pw TEXT, salt TEXT,
      status TEXT DEFAULT 'active', invite TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS invites(
      code TEXT PRIMARY KEY, status TEXT DEFAULT 'unused',
      used_by TEXT, created TEXT, used_at TEXT, scope TEXT DEFAULT 'stock_member');
    CREATE TABLE IF NOT EXISTS tokens(
      thash TEXT PRIMARY KEY, user_id INTEGER, created REAL, last_seen REAL);
    CREATE TABLE IF NOT EXISTS permissions(
      user_id INTEGER, permission TEXT, created TEXT,
      PRIMARY KEY(user_id, permission));
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    invite_cols = {row[1] for row in c.execute("PRAGMA table_info(invites)")}
    if "scope" not in invite_cols:
        c.execute("ALTER TABLE invites ADD COLUMN scope TEXT DEFAULT 'stock_member'")
    # 只迁移一次：升级前的老用户保持原会员能力；升级后新账号完全按邀请码权限发放。
    migrated = c.execute("SELECT value FROM meta WHERE key='permissions_v1'").fetchone()
    if not migrated:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "INSERT OR IGNORE INTO permissions(user_id,permission,created) "
            "SELECT id,'stock_member',? FROM users",
            (now,),
        )
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('permissions_v1','done')")
    c.commit()
    c.close()


def hpw(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def thash(t):
    return hashlib.sha256(t.encode()).hexdigest()


FAILS, FLOCK = {}, threading.Lock()


def too_many(ip):
    now = time.time()
    with FLOCK:
        FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < 3600]
        return len(FAILS[ip]) >= MAX_FAILS_PER_HOUR


def fail(ip):
    with FLOCK:
        FAILS.setdefault(ip, []).append(time.time())


def load_points():
    try:
        with open(POINTS, encoding="utf-8") as f:
            d = json.load(f)
        # 兼容 2026-07 旧版 buypoints.json（顶层直接是 code -> 观察位文字）。
        if isinstance(d, dict) and not ({"buy", "sell", "tgt"} & set(d)):
            return {"buy": d, "sell": {}, "tgt": {}}
        return {"buy": d.get("buy", {}), "sell": d.get("sell", {}), "tgt": d.get("tgt", {})}
    except Exception:
        return {"buy": {}, "sell": {}, "tgt": {}}


def load_reports():
    """研报目录。元数据（标题/标的/日期/简介）是公开橱窗，PDF 本体只走 /api/report 验 token。"""
    try:
        with open(REPORTS_JSON, encoding="utf-8") as f:
            return json.load(f).get("reports", [])
    except Exception:
        return []


def load_kedu_points():
    try:
        with open(KEDU_POINTS, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"items": []}
    except Exception:
        return {"items": []}


def load_live_quotes():
    try:
        with open(LIVE_QUOTES, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"q": {}}
    except Exception:
        return {"q": {}}


def load_static_quotes():
    try:
        with open(STATIC_QUOTES, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"sections": []}
    except Exception:
        return {"sections": []}


def _currency_for(market, sample=""):
    market = str(market or "").strip()
    by_market = {"美股": "USD", "A股": "CNY", "港股": "HKD", "日股": "JPY", "韩股": "KRW"}
    if market in by_market:
        return by_market[market]
    text = str(sample or "")
    if "HK$" in text:
        return "HKD"
    if "$" in text:
        return "USD"
    if "¥" in text or "￥" in text:
        return "CNY"
    return ""


def _static_identity_index():
    """把公开行情快照转换成代码索引；只读取名称、市场、币种，不暴露任何私有点位。"""
    index = {}
    for section in load_static_quotes().get("sections", []):
        for row in section.get("rows", []):
            if not isinstance(row, list) or len(row) < 6:
                continue
            code = str(row[1] or "").strip().upper()
            if not code:
                continue
            market = str(row[2] or "").strip()
            index[code] = {
                "company": str(row[0] or code).strip(),
                "market": market,
                "currency": _currency_for(market, row[5]),
            }
    return index


def _point_number(value):
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_observation_text(value):
    """保守解析财多多观察位。

    能确认的单点／区间才进入 bands；无法确认时只返回原文，绝不从年份、百分比或说明文字猜数字。
    """
    if isinstance(value, dict):
        raw = value.get("text") or value.get("raw") or value.get("value") or ""
    else:
        raw = value
    text = str(raw or "").strip()
    if not text:
        return {"status": "current", "text": "", "bands": [], "threshold": None, "parse_status": "raw_only"}
    clean = re.sub(r"（[^）]*）|\([^)]*\)", " ", text)
    parts = [part.strip() for part in re.split(r"[/／;；、]", clean) if part.strip()]
    range_re = re.compile(
        r"(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s*(?:-|–|—|~|～|至|到)\s*(\d[\d,]*(?:\.\d+)?)(?![\d%])"
    )
    number_re = re.compile(r"(?<![\d.])(\d[\d,]*(?:\.\d+)?)(?![\d%])")
    bands = []
    for part in parts[:6]:
        match = range_re.search(part)
        if match:
            values = [_point_number(match.group(1)), _point_number(match.group(2))]
            if all(values):
                bands.append(sorted(values))
                continue
        match = number_re.search(part)
        if not match:
            continue
        tail = part[match.end():].lstrip()
        number = _point_number(match.group(1))
        if number is None or tail.startswith("%") or tail.startswith("年"):
            continue
        bands.append([number, number])
    return {
        "status": "current",
        "text": text,
        "bands": bands,
        "threshold": bands[0][1] if bands else None,
        "parse_status": "parsed" if bands else "raw_only",
        "source_ref": "财多多点位台账",
    }


def _normal_band(value):
    if not isinstance(value, (list, tuple)) or not value:
        return None
    numbers = [_point_number(item) for item in value]
    numbers = [item for item in numbers if item is not None]
    if not numbers:
        return None
    return [min(numbers), max(numbers)]


def _source_relation(caiduoduo, kedu):
    caido_band = _normal_band((caiduoduo or {}).get("bands", [None])[0]) if (caiduoduo or {}).get("bands") else None
    kedu_band = _normal_band(((kedu or {}).get("bands") or {}).get("b1"))
    if not caido_band or not kedu_band:
        return "uncomparable"
    if max(caido_band[0], kedu_band[0]) <= min(caido_band[1], kedu_band[1]):
        return "overlap"
    caido_mid = sum(caido_band) / 2
    kedu_mid = sum(kedu_band) / 2
    gap_ratio = abs(caido_mid - kedu_mid) / max(caido_mid, kedu_mid)
    if gap_ratio <= 0.10:
        return "near"
    if gap_ratio > 0.20:
        return "divergent"
    return "different"


def _bands_live(bands, price):
    live_bands = []
    for band in bands or []:
        values = _normal_band(band)
        if not values:
            continue
        lo, hi = values
        live_bands.append({
            "range": values,
            "distance_pct": [_pct(lo, price), _pct(hi, price)] if price else None,
            "inside": bool(price and lo <= price <= hi),
        })
    state = "price_unavailable"
    if price and live_bands:
        inside = next((index for index, band in enumerate(live_bands) if band["inside"]), None)
        if inside is not None:
            state = "inside_b" + str(inside + 1)
        else:
            top = max(band["range"][1] for band in live_bands)
            bottom = min(band["range"][0] for band in live_bands)
            state = "above_bands" if price > top else ("below_bands" if price < bottom else "between_bands")
    return live_bands, state


def _quote_live(keys):
    quote = _fresh_live_quote(keys) or _static_quote(keys)
    return {
        "price": quote.get("p") if quote else None,
        "change_pct": quote.get("c") if quote else None,
        "updated_at": quote.get("updated_at") if quote else None,
        "source": quote.get("src") if quote else None,
        "price_mode": quote.get("mode") if quote else "unavailable",
        "price_status": "ok" if quote and quote.get("p") else "unavailable",
    }


def aggregate_kedu_points():
    """动态组成刻度点位总表；两来源只并列，不平均、不覆盖。"""
    kedu_data = load_kedu_points()
    kedu_items = [item for item in kedu_data.get("items", []) if item.get("status") == "current"]
    aliases = {}
    for item in kedu_items:
        for key in [item.get("code", ""), *(item.get("aliases") or [])]:
            if str(key).strip():
                aliases[str(key).strip().upper()] = item
    caido_by_canonical = {}
    for raw_code, raw_value in load_points().get("buy", {}).items():
        code = str(raw_code or "").strip().upper()
        if not code:
            continue
        matched = aliases.get(code)
        canonical = str((matched or {}).get("code") or code).strip().upper()
        caido_by_canonical[canonical] = parse_observation_text(raw_value)
    kedu_by_code = {str(item.get("code") or "").strip().upper(): item for item in kedu_items if item.get("code")}
    identities = _static_identity_index()
    rows = []
    for code in sorted(set(caido_by_canonical) | set(kedu_by_code)):
        kedu = kedu_by_code.get(code)
        caido = caido_by_canonical.get(code)
        keys = [code] + list((kedu or {}).get("aliases") or [])
        identity = identities.get(code) or next((identities.get(str(key).upper()) for key in keys if identities.get(str(key).upper())), {})
        market = (kedu or {}).get("market") or identity.get("market") or ""
        live = _quote_live(keys)
        caido_live_bands, caido_state = _bands_live((caido or {}).get("bands"), live["price"])
        live["caiduoduo"] = {"state": caido_state, "bands": caido_live_bands}
        if kedu:
            kedu_live = enrich_kedu_point(kedu).get("live", {})
            live["kedu"] = {
                "state": kedu_live.get("state"),
                "bands": kedu_live.get("bands", {}),
                "scenarios": kedu_live.get("scenarios", {}),
            }
        else:
            live["kedu"] = None
        relation = _source_relation(caido, kedu)
        rows.append({
            "code": code,
            "aliases": list((kedu or {}).get("aliases") or []),
            "company": (kedu or {}).get("company") or identity.get("company") or code,
            "market": market,
            "currency": (kedu or {}).get("currency") or identity.get("currency") or _currency_for(market),
            "sources": {"caiduoduo": caido, "kedu": dict(kedu) if kedu else None},
            "relation": relation,
            "calibration": {"status": "confirmed", "relation": relation},
            "live": live,
        })
    return rows


def _market_number(value):
    """把 quotes.json 的展示字符串还原成数值；空值永远保持 None，不能变成 0。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("−", "-")
    if not text or text in {"-", "—", "获取失败"}:
        return None
    match = re.search(r"-?(?:\d+(?:\.\d*)?|\.\d+)", text)
    return float(match.group()) if match else None


def _static_quote(keys):
    """全市场最近收盘价底座。quotes.json 的每行第 1 位是代码、第 5 位是现价。"""
    wanted = {str(key).strip().upper() for key in keys if str(key).strip()}
    data = load_static_quotes()
    for section in data.get("sections", []):
        for row in section.get("rows", []):
            if not isinstance(row, list) or len(row) < 6 or str(row[1]).upper() not in wanted:
                continue
            price = _market_number(row[5])
            if price is None or price <= 0:
                continue
            return {
                "p": price,
                "c": _market_number(row[16]) if len(row) > 16 else None,
                "updated_at": data.get("updated"),
                "src": "daily_close",
                "mode": "close",
            }
    return None


def _fresh_live_quote(keys):
    """美股盘前/盘后覆盖层；超过 10 分钟未刷新就退回收盘价，不沿用陈旧快照。"""
    data = load_live_quotes()
    timestamp = _market_number(data.get("t"))
    age = time.time() - timestamp if timestamp is not None else None
    if age is None or age < -300 or age > LIVE_MAX_AGE_SECONDS:
        return None
    quotes = data.get("q", {}) if isinstance(data.get("q", {}), dict) else {}
    for key in keys:
        quote = quotes.get(str(key).upper()) or quotes.get(str(key))
        price = _market_number(quote.get("p")) if isinstance(quote, dict) else None
        if price is None or price <= 0:
            continue
        return {
            "p": price,
            "c": _market_number(quote.get("c")),
            "updated_at": time.strftime("%Y-%m-%d %H:%M", time.gmtime(timestamp + 8 * 3600)) + " 北京时间",
            "src": data.get("src") or "extended_market",
            "mode": "extended",
        }
    return None


def permissions_for(user_id):
    c = db()
    rows = c.execute("SELECT permission FROM permissions WHERE user_id=? ORDER BY permission", (user_id,)).fetchall()
    c.close()
    return [row[0] for row in rows if row[0] in VALID_PERMISSIONS]


def normalize_scopes(raw):
    values = {value.strip() for value in str(raw or "").split(",") if value.strip()}
    return sorted(values & VALID_PERMISSIONS)


def _pct(target, base):
    if not target or not base:
        return None
    return round((float(target) / float(base) - 1) * 100, 1)


def enrich_kedu_point(item):
    """只给单家公司加实时状态；不生成任何可枚举的点位全集。"""
    keys = [item.get("code", "")] + list(item.get("aliases") or [])
    # 全市场收盘价兜底，美股盘前/盘后价只在文件仍新鲜时覆盖。
    quote = _fresh_live_quote(keys) or _static_quote(keys)
    price = quote.get("p") if quote else None
    bands = {}
    for key, values in (item.get("bands") or {}).items():
        if not values:
            continue
        lo, hi = sorted(float(value) for value in values)
        bands[key] = {
            "range": [lo, hi],
            "distance_pct": [_pct(lo, price), _pct(hi, price)] if price else None,
            "inside": bool(price and lo <= price <= hi),
        }
    state = "price_unavailable"
    if price and bands:
        inside = next((key for key, value in bands.items() if value["inside"]), None)
        if inside:
            state = "inside_" + inside
        else:
            top = max(value["range"][1] for value in bands.values())
            bottom = min(value["range"][0] for value in bands.values())
            state = "above_bands" if price > top else ("below_bands" if price < bottom else "between_bands")
    scenarios = {}
    entry = (item.get("bands") or {}).get("b2") or (item.get("bands") or {}).get("b1")
    for key, target in (item.get("scenarios") or {}).items():
        if target is None:
            continue
        entry_returns = sorted(_pct(target, value) for value in entry) if entry else None
        scenarios[key] = {
            "target": target,
            "return_from_price_pct": _pct(target, price) if price else None,
            "return_from_entry_pct": entry_returns,
        }
    enriched = dict(item)
    enriched["live"] = {
        "price": price,
        "change_pct": quote.get("c") if quote else None,
        "updated_at": quote.get("updated_at") if quote else None,
        "source": quote.get("src") if quote else None,
        "price_mode": quote.get("mode") if quote else "unavailable",
        "price_status": "ok" if price else "unavailable",
        "state": state,
        "bands": bands,
        "scenarios": scenarios,
    }
    return enriched


class H(BaseHTTPRequestHandler):
    def _cors(self):
        o = self.headers.get("Origin", "")
        if o in ALLOW_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 10000:
                return None
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def _ip(self):
        return self.headers.get("X-Real-IP") or self.client_address[0]

    def _user(self):
        a = self.headers.get("Authorization", "")
        if not a.startswith("Bearer "):
            return None
        th = thash(a[7:])
        c = db()
        row = c.execute(
            "SELECT u.id,u.username,u.status,t.created FROM tokens t "
            "JOIN users u ON u.id=t.user_id WHERE t.thash=?", (th,)).fetchone()
        ok = row and time.time() - row[3] < TOKEN_DAYS * 86400 and row[2] == "active"
        if ok:
            c.execute("UPDATE tokens SET last_seen=? WHERE thash=?", (time.time(), th))
            c.commit()
        c.close()
        return {"id": row[0], "username": row[1]} if ok else None

    def _issue(self, c, uid):
        t = secrets.token_urlsafe(32)
        c.execute("INSERT INTO tokens VALUES(?,?,?,?)", (thash(t), uid, time.time(), time.time()))
        return t

    def _permission_user(self, permission, login_message="请先登录"):
        u = self._user()
        if not u:
            self._json(401, {"ok": False, "err": login_message})
            return None
        if permission not in permissions_for(u["id"]):
            self._json(403, {"ok": False, "err": "当前账号没有此项权限"})
            return None
        return u

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/health":
            return self._json(200, {"ok": True, "ts": int(time.time())})
        if p == "/api/kedu/config":
            return self._json(200, {
                "ok": True,
                "enabled": KEDU_ENABLED,
                "points_table_enabled": KEDU_TABLE_ENABLED,
            })
        if p == "/api/public":
            pts = load_points()
            codes = sorted(set(pts["buy"]) | set(pts["sell"]) | set(pts["tgt"]))
            rcodes = sorted({c for r in load_reports() for c in r.get("codes", [])})
            return self._json(200, {"ok": True, "codes": codes, "rcodes": rcodes})
        if p == "/api/reports":
            cat = [{k: r.get(k) for k in ("id", "codes", "title", "date", "src", "pages", "d")}
                   for r in load_reports()]
            return self._json(200, {"ok": True, "reports": cat})
        if p == "/api/report-html":
            u = self._permission_user("stock_member", "研报为会员专属，请先登录")
            if not u:
                return
            rid = (parse_qs(urlparse(self.path).query).get("id") or [""])[-1]
            rec = next((r for r in load_reports() if r.get("id") == rid), None)
            if not rec:
                return self._json(404, {"ok": False, "err": "报告不存在"})
            hp = os.path.join(MEMBER, "html", os.path.basename(rid) + ".html")
            if not os.path.isfile(hp):
                return self._json(200, {"ok": True, "html": ""})   # 正文尚未整理：前端提示下载PDF
            with open(hp, encoding="utf-8") as f:
                return self._json(200, {"ok": True, "html": f.read()})
        if p == "/api/report":
            u = self._permission_user("stock_member", "研报为会员专属，请先登录")
            if not u:
                return
            rid = (parse_qs(urlparse(self.path).query).get("id") or [""])[-1]
            rec = next((r for r in load_reports() if r.get("id") == rid), None)
            if not rec:
                return self._json(404, {"ok": False, "err": "报告不存在"})
            fp = os.path.join(REPORTS_DIR, os.path.basename(rec.get("file", "")))
            if not os.path.isfile(fp):
                return self._json(404, {"ok": False, "err": "报告文件缺失，请联系烟囱"})
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", "inline; filename=report.pdf")
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return
        if p == "/api/report-audio":
            u = self._permission_user("stock_member", "研报音频为会员专属，请先登录")
            if not u:
                return
            rid = (parse_qs(urlparse(self.path).query).get("id") or [""])[-1]
            rec = next((r for r in load_reports() if r.get("id") == rid), None)
            if not rec:
                return self._json(404, {"ok": False, "err": "报告不存在"})
            fp = os.path.join(MEMBER, "audio", os.path.basename(rid) + ".mp3")
            if not os.path.isfile(fp):
                return self._json(404, {"ok": False, "err": "该报告暂无音频"})
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return
        if p == "/api/report-audio-stream":
            # 票据直链流式（2026-07-19）：短时效签名票据替代Bearer头，浏览器媒体栈可直连→即点即播+Range拖拽。
            q = parse_qs(urlparse(self.path).query)
            rid = (q.get("id") or [""])[-1]
            tkt = (q.get("t") or [""])[-1]
            c = db()
            c.execute("CREATE TABLE IF NOT EXISTS audio_tickets(th TEXT PRIMARY KEY, rid TEXT, created REAL)")
            row = c.execute("SELECT rid, created FROM audio_tickets WHERE th=?", (thash(tkt),)).fetchone()
            c.close()
            if not row or row[0] != rid or time.time() - row[1] > 21600:
                return self._json(403, {"ok": False, "err": "播放票据无效或已过期，请刷新页面"})
            fp = os.path.join(MEMBER, "audio", os.path.basename(rid) + ".mp3")
            if not os.path.isfile(fp):
                return self._json(404, {"ok": False, "err": "该报告暂无音频"})
            size = os.path.getsize(fp)
            rng = self.headers.get("Range", "")
            start, end = 0, size - 1
            partial = False
            if rng.startswith("bytes="):
                m = re.match(r"bytes=(\d*)-(\d*)", rng)
                if m:
                    if m.group(1): start = int(m.group(1))
                    if m.group(2): end = min(int(m.group(2)), size - 1)
                    if not m.group(1) and m.group(2):   # bytes=-N 尾部
                        start = max(0, size - int(m.group(2))); end = size - 1
                    partial = True
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self._cors(); self.end_headers(); return
            length = end - start + 1
            self.send_response(206 if partial else 200)
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            with open(fp, "rb") as f:
                f.seek(start)
                remain = length
                while remain > 0:
                    chunk = f.read(min(65536, remain))
                    if not chunk: break
                    try:
                        self.wfile.write(chunk)
                    except Exception:
                        break   # 客户端拖进度条会中断连接，正常现象
                    remain -= len(chunk)
            return
        if p == "/api/odds":
            # 烟囱自用·买卖点赔率汇总（2026-08-16）：会员专属，数据 member/odds.json（私有仓库同步）
            u = self._permission_user("stock_member", "会员专属，请先登录")
            if not u:
                return
            op = os.path.join(MEMBER, "odds.json")
            if not os.path.isfile(op):
                return self._json(200, {"ok": True, "items": []})
            with open(op, encoding="utf-8") as f:
                return self._json(200, {"ok": True, **json.load(f)})
        if p == "/api/points":
            u = self._permission_user("stock_member", "未登录或登录已过期")
            if not u:
                return
            pts = load_points()
            return self._json(200, {"ok": True, "user": u["username"], **pts})
        if p == "/api/kedu/point":
            if not KEDU_ENABLED:
                return self._json(404, {"ok": False, "err": "该功能尚未开放"})
            u = self._permission_user("kedu_points", "点位为受邀用户专属，请先登录")
            if not u:
                return
            code = (parse_qs(urlparse(self.path).query).get("code") or [""])[-1].strip().upper()
            if not code or len(code) > 24:
                return self._json(400, {"ok": False, "err": "缺少有效代码"})
            data = load_kedu_points()
            found = None
            for item in data.get("items", []):
                aliases = {str(item.get("code") or "").upper(), *(str(value).upper() for value in item.get("aliases") or [])}
                if code in aliases:
                    found = item
                    break
            if not found:
                return self._json(404, {"ok": False, "err": "该公司暂无已校准点位"})
            return self._json(200, {"ok": True, "item": enrich_kedu_point(found)})
        if p == "/api/kedu/points":
            if not KEDU_TABLE_ENABLED:
                return self._json(404, {"ok": False, "err": "该功能尚未开放"})
            u = self._permission_user("kedu_points", "点位为受邀用户专属，请先登录")
            if not u:
                return
            data = load_kedu_points()
            return self._json(200, {
                "ok": True,
                "updated_at": data.get("generated_at") or data.get("updated_at"),
                "items": aggregate_kedu_points(),
            })
        if p == "/api/me":
            u = self._user()
            return self._json(200, {
                "ok": bool(u),
                "user": u["username"] if u else None,
                "permissions": permissions_for(u["id"]) if u else [],
            })
        return self._json(404, {"ok": False, "err": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        ip = self._ip()
        b = self._body()
        if b is None:
            return self._json(400, {"ok": False, "err": "bad request"})

        if p == "/api/audio-ticket":
            # 签发播放票据（2026-07-19）：会员验票后发短时效票（6小时·仅该报告·与登录token无关），供媒体栈直链流式。
            u = self._permission_user("stock_member", "会员专属，请先登录")
            if not u:
                return
            rid = str((b or {}).get("id") or "")
            rec = next((r for r in load_reports() if r.get("id") == rid), None)
            if not rec:
                return self._json(404, {"ok": False, "err": "报告不存在"})
            t = secrets.token_urlsafe(24)
            c = db()
            c.execute("CREATE TABLE IF NOT EXISTS audio_tickets(th TEXT PRIMARY KEY, rid TEXT, created REAL)")
            c.execute("DELETE FROM audio_tickets WHERE created < ?", (time.time() - 21600,))
            c.execute("INSERT INTO audio_tickets VALUES(?,?,?)", (thash(t), rid, time.time()))
            c.commit(); c.close()
            return self._json(200, {"ok": True, "t": t, "ttl": 21600})

        if p == "/api/debug":
            # 真机黑匣子：接收手机端事件回放（排障用），落盘 debug/ 目录，≤20KB
            if too_many(ip):
                return self._json(429, {"ok": False})
            raw = json.dumps(b, ensure_ascii=False)
            if len(raw) > 20000:
                return self._json(400, {"ok": False, "err": "too big"})
            d = os.path.join(BASE, "debug")
            os.makedirs(d, exist_ok=True)
            fn = time.strftime("%Y%m%d-%H%M%S") + "-" + ip.replace(":", "_") + ".json"
            with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
                f.write(raw)
            return self._json(200, {"ok": True})

        if p == "/api/register":
            if too_many(ip):
                return self._json(429, {"ok": False, "err": "尝试过于频繁，请1小时后再试"})
            code = str(b.get("invite", "")).strip().upper()
            un = str(b.get("username", "")).strip()
            pw = str(b.get("password", ""))
            if not re.fullmatch(r"[\w一-龥.\-]{2,20}", un):
                return self._json(400, {"ok": False, "err": "用户名需2-20位（中英文、数字、._-）"})
            if len(pw) < 6:
                return self._json(400, {"ok": False, "err": "密码至少6位"})
            c = db()
            iv = c.execute("SELECT status,scope FROM invites WHERE code=?", (code,)).fetchone()
            if not iv or iv[0] != "unused":
                fail(ip)
                c.close()
                return self._json(400, {"ok": False, "err": "邀请码无效或已被使用"})
            if c.execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone():
                c.close()
                return self._json(400, {"ok": False, "err": "用户名已被占用，换一个吧"})
            salt = secrets.token_hex(16)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            c.execute("INSERT INTO users(username,pw,salt,invite,created) VALUES(?,?,?,?,?)",
                      (un, hpw(pw, salt), salt, code, now))
            uid = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()[0]
            scopes = normalize_scopes(iv[1])
            if not scopes:
                c.close()
                return self._json(400, {"ok": False, "err": "邀请码权限配置无效，请联系烟囱"})
            c.executemany(
                "INSERT OR IGNORE INTO permissions(user_id,permission,created) VALUES(?,?,?)",
                [(uid, permission, now) for permission in scopes],
            )
            c.execute("UPDATE invites SET status='used',used_by=?,used_at=? WHERE code=?",
                      (un, now, code))
            t = self._issue(c, uid)
            c.commit()
            c.close()
            return self._json(200, {"ok": True, "token": t, "user": un, "permissions": scopes})

        if p == "/api/login":
            if too_many(ip):
                return self._json(429, {"ok": False, "err": "尝试过于频繁，请1小时后再试"})
            un = str(b.get("username", "")).strip()
            pw = str(b.get("password", ""))
            c = db()
            row = c.execute("SELECT id,pw,salt,status FROM users WHERE username=?", (un,)).fetchone()
            if not row or hpw(pw, row[2]) != row[1]:
                fail(ip)
                c.close()
                return self._json(400, {"ok": False, "err": "用户名或密码不对"})
            if row[3] != "active":
                c.close()
                return self._json(403, {"ok": False, "err": "账号已停用，请联系烟囱"})
            t = self._issue(c, row[0])
            c.commit()
            c.close()
            return self._json(200, {
                "ok": True,
                "token": t,
                "user": un,
                "permissions": permissions_for(row[0]),
            })

        return self._json(404, {"ok": False, "err": "not found"})

    def log_message(self, fmt, *a):
        pass


if __name__ == "__main__":
    init()
    print(f"stockauth listening on 127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
