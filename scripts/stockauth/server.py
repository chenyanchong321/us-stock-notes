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
DB = os.path.join(BASE, "auth.db")
POINTS = os.path.join(BASE, "points.json")
MEMBER = os.path.join(BASE, "member")          # us-stock-member 私有仓库的克隆（cron 定时 git pull）
REPORTS_JSON = os.path.join(MEMBER, "reports.json")
REPORTS_DIR = os.path.join(MEMBER, "reports")
PORT = 8600
ALLOW_ORIGINS = {"https://stock.ziyuanai.top", "https://www.ziyuanai.top",
                 "https://chenyanchong321.github.io"}
TOKEN_DAYS = 400          # 登录有效期：够长，熟人产品不折腾
MAX_FAILS_PER_HOUR = 30   # 单IP每小时最多失败次数（防撞库）


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
      used_by TEXT, created TEXT, used_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(
      thash TEXT PRIMARY KEY, user_id INTEGER, created REAL, last_seen REAL);
    """)
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

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/health":
            return self._json(200, {"ok": True, "ts": int(time.time())})
        if p == "/api/public":
            pts = load_points()
            codes = sorted(set(pts["buy"]) | set(pts["sell"]) | set(pts["tgt"]))
            rcodes = sorted({c for r in load_reports() for c in r.get("codes", [])})
            return self._json(200, {"ok": True, "codes": codes, "rcodes": rcodes})
        if p == "/api/reports":
            cat = [{k: r.get(k) for k in ("id", "codes", "title", "date", "src", "pages", "d")}
                   for r in load_reports()]
            return self._json(200, {"ok": True, "reports": cat})
        if p == "/api/report":
            u = self._user()
            if not u:
                return self._json(401, {"ok": False, "err": "研报为会员专属，请先登录"})
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
        if p == "/api/points":
            u = self._user()
            if not u:
                return self._json(401, {"ok": False, "err": "未登录或登录已过期"})
            pts = load_points()
            return self._json(200, {"ok": True, "user": u["username"], **pts})
        if p == "/api/me":
            u = self._user()
            return self._json(200, {"ok": bool(u), "user": u["username"] if u else None})
        return self._json(404, {"ok": False, "err": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        ip = self._ip()
        b = self._body()
        if b is None:
            return self._json(400, {"ok": False, "err": "bad request"})

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
            iv = c.execute("SELECT status FROM invites WHERE code=?", (code,)).fetchone()
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
            c.execute("UPDATE invites SET status='used',used_by=?,used_at=? WHERE code=?",
                      (un, now, code))
            t = self._issue(c, uid)
            c.commit()
            c.close()
            return self._json(200, {"ok": True, "token": t, "user": un})

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
            return self._json(200, {"ok": True, "token": t, "user": un})

        return self._json(404, {"ok": False, "err": "not found"})

    def log_message(self, fmt, *a):
        pass


if __name__ == "__main__":
    init()
    print(f"stockauth listening on 127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
