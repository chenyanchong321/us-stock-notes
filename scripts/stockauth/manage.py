#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stockauth 命令行管理（在 ECS 上：cd /root/stockauth && python3 manage.py <命令>）

  invites N [权限] 生成 N 个邀请码；默认 stock_member，可填 kedu_points 或逗号分隔两项
  invlist          所有邀请码及使用状态
  users            所有用户（含状态、注册时间、邀请码和权限）
  perms 用户名      查看一个用户的权限
  grant 用户名 权限 增加权限（stock_member / kedu_points）
  revoke 用户名 权限 撤销权限并踢掉该用户现有登录
  ban 用户名        停用账号（立即失去点位访问）
  unban 用户名      恢复账号
  resetpw 用户名 新密码   重置密码（用户忘密码时用）
  import 文件      导入点位。支持两种格式：
                   ① 网站旧版 buypoints.json（{代码:文案} → 全部当作 buy）
                   ② {"buy":{...},"sell":{...}}
  setbuy 代码 文案  单条增改买点/观察位（文案为空=删除）
  setsell 代码 文案 单条增改卖点（文案为空=删除）
  settgt 代码 文案  单条增改目标价（文案为空=删除；第一个数字会被前端解析为目标价）
  show             当前点位（buy/sell 全量）
  export           备份点位+用户库到 /root/stockauth/backup-<日期>/
"""
import json, os, sys, sqlite3, secrets, hashlib, time, shutil

import server as stockauth_server

BASE = os.path.dirname(os.path.abspath(__file__))
DB = stockauth_server.DB
POINTS = stockauth_server.POINTS
KEDU_POINTS = stockauth_server.KEDU_POINTS
VALID_PERMISSIONS = stockauth_server.VALID_PERMISSIONS


def db():
    return sqlite3.connect(DB)


def load():
    try:
        with open(POINTS, encoding="utf-8") as f:
            d = json.load(f)
        return {"buy": d.get("buy", {}), "sell": d.get("sell", {}), "tgt": d.get("tgt", {})}
    except Exception:
        return {"buy": {}, "sell": {}, "tgt": {}}


def save(pts):
    tmp = POINTS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pts, f, ensure_ascii=False, indent=1)
    os.replace(tmp, POINTS)
    print(f"已保存：buy {len(pts['buy'])} 条 / sell {len(pts['sell'])} 条 / tgt {len(pts.get('tgt',{}))} 条")


def main():
    stockauth_server.init()
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    cmd = a[0]
    if cmd == "invites":
        n = int(a[1]) if len(a) > 1 else 10
        scope = a[2] if len(a) > 2 else "stock_member"
        scopes = stockauth_server.normalize_scopes(scope)
        if not scopes or set(scopes) != {value.strip() for value in scope.split(",") if value.strip()}:
            sys.exit("权限只能是 stock_member、kedu_points，或用逗号组合两项")
        scope = ",".join(scopes)
        c = db()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        out = []
        for _ in range(n):
            code = "YC-" + secrets.token_hex(2).upper() + "-" + secrets.token_hex(2).upper()
            c.execute("INSERT INTO invites(code,created,scope) VALUES(?,?,?)", (code, now, scope))
            out.append(code)
        c.commit()
        c.close()
        print("\n".join(out))
    elif cmd == "invlist":
        for r in db().execute("SELECT code,status,used_by,used_at,scope FROM invites ORDER BY created"):
            print(f"{r[0]}  {r[1]:6s}  {r[2] or '':10s} {r[3] or ''}  权限 {r[4] or 'stock_member'}")
    elif cmd == "users":
        c = db()
        for r in c.execute("SELECT id,username,status,created,invite FROM users ORDER BY id"):
            perms = ",".join(row[0] for row in c.execute("SELECT permission FROM permissions WHERE user_id=? ORDER BY permission", (r[0],))) or "无"
            print(f"#{r[0]:<3d} {r[1]:16s} {r[2]:8s} 注册 {r[3]}  码 {r[4]}  权限 {perms}")
        c.close()
    elif cmd == "perms":
        if len(a) < 2:
            sys.exit("用法：manage.py perms 用户名")
        c = db()
        row = c.execute("SELECT id FROM users WHERE username=?", (a[1],)).fetchone()
        if not row:
            c.close(); sys.exit(f"没找到用户 {a[1]}")
        print("\n".join(stockauth_server.permissions_for(row[0])) or "无权限")
        c.close()
    elif cmd in ("grant", "revoke"):
        if len(a) < 3 or a[2] not in VALID_PERMISSIONS:
            sys.exit("用法：manage.py grant|revoke 用户名 stock_member|kedu_points")
        un, permission = a[1], a[2]
        c = db()
        row = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()
        if not row:
            c.close(); sys.exit(f"没找到用户 {un}")
        if cmd == "grant":
            c.execute(
                "INSERT OR IGNORE INTO permissions(user_id,permission,created) VALUES(?,?,?)",
                (row[0], permission, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
        else:
            c.execute("DELETE FROM permissions WHERE user_id=? AND permission=?", (row[0], permission))
            c.execute("DELETE FROM tokens WHERE user_id=?", (row[0],))
        c.commit(); c.close()
        print(f"{un} -> {'增加' if cmd == 'grant' else '撤销'} {permission}")
    elif cmd in ("ban", "unban"):
        st = "banned" if cmd == "ban" else "active"
        c = db()
        c.execute("UPDATE users SET status=? WHERE username=?", (st, a[1]))
        c.commit()
        print(f"{a[1]} -> {st}" if c.total_changes else f"没找到用户 {a[1]}")
    elif cmd == "resetpw":
        un, pw = a[1], a[2]
        if len(pw) < 6:
            sys.exit("密码至少6位")
        salt = secrets.token_hex(16)
        h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        c = db()
        c.execute("UPDATE users SET pw=?,salt=? WHERE username=?", (h, salt, un))
        # 踢掉旧登录
        c.execute("DELETE FROM tokens WHERE user_id=(SELECT id FROM users WHERE username=?)", (un,))
        c.commit()
        print(f"{un} 密码已重置" if c.total_changes else f"没找到用户 {un}")
    elif cmd == "import":
        with open(a[1], encoding="utf-8") as f:
            d = json.load(f)
        pts = {"buy": d.get("buy", {}), "sell": d.get("sell", {}), "tgt": d.get("tgt", {})} if "buy" in d else {"buy": d, "sell": {}, "tgt": {}}
        save(pts)
    elif cmd in ("setbuy", "setsell", "settgt"):
        k = "buy" if cmd == "setbuy" else ("sell" if cmd == "setsell" else "tgt")
        pts = load()
        code, txt = a[1], " ".join(a[2:])
        if txt:
            pts[k][code] = txt
        else:
            pts[k].pop(code, None)
        save(pts)
    elif cmd == "show":
        print(json.dumps(load(), ensure_ascii=False, indent=1))
    elif cmd == "export":
        d = os.path.join(BASE, "backup-" + time.strftime("%Y%m%d"))
        os.makedirs(d, exist_ok=True)
        for f in ("auth.db", "points.json"):
            p = os.path.join(BASE, f)
            if os.path.exists(p):
                shutil.copy2(p, d)
        if os.path.exists(KEDU_POINTS):
            shutil.copy2(KEDU_POINTS, os.path.join(d, "kedu_points.json"))
        print("已备份到", d)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
