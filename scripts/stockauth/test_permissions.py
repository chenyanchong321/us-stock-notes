import json
import hashlib
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server


class PermissionMigrationTest(unittest.TestCase):
    def test_legacy_users_keep_stock_member_permission_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_db = server.DB
            server.DB = str(Path(tmp) / "legacy.db")
            c = sqlite3.connect(server.DB)
            c.executescript(
                """
                CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, pw TEXT, salt TEXT,
                  status TEXT DEFAULT 'active', invite TEXT, created TEXT);
                CREATE TABLE invites(code TEXT PRIMARY KEY, status TEXT DEFAULT 'unused',
                  used_by TEXT, created TEXT, used_at TEXT);
                CREATE TABLE tokens(thash TEXT PRIMARY KEY, user_id INTEGER, created REAL, last_seen REAL);
                INSERT INTO users(id,username,status) VALUES(1,'legacy','active');
                """
            )
            c.commit(); c.close()
            try:
                server.init()
                self.assertEqual(server.permissions_for(1), ["stock_member"])
                c = sqlite3.connect(server.DB)
                self.assertIn("scope", {row[1] for row in c.execute("PRAGMA table_info(invites)")})
                # 再启动一次不能给升级后新建的账号擅自补权限。
                c.execute("INSERT INTO users(id,username,status) VALUES(2,'newer','active')")
                c.commit(); c.close()
                server.init()
                self.assertEqual(server.permissions_for(2), [])
            finally:
                server.DB = old_db


class PermissionHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old = (
            server.DB,
            server.POINTS,
            server.KEDU_POINTS,
            server.LIVE_QUOTES,
            server.STATIC_QUOTES,
            server.KEDU_ENABLED,
            server.KEDU_TABLE_ENABLED,
            server.hpw,
        )
        server.DB = str(root / "auth.db")
        server.POINTS = str(root / "points.json")
        server.KEDU_POINTS = str(root / "kedu_points.json")
        server.LIVE_QUOTES = str(root / "live.json")
        server.STATIC_QUOTES = str(root / "quotes.json")
        server.KEDU_ENABLED = True
        server.KEDU_TABLE_ENABLED = True
        # macOS 自带的旧 Python 没编译 scrypt；这里只替换测试散列，不改变生产实现。
        server.hpw = lambda password, salt: hashlib.sha256((salt + password).encode()).hexdigest()
        Path(server.KEDU_POINTS).write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "code": "AVAV",
                            "aliases": ["AVAV"],
                            "company": "AeroVironment",
                            "market": "美股",
                            "currency": "USD",
                            "bands": {"b1": [158, 170], "b2": [136, 150]},
                            "scenarios": {"bear": 105, "base": 200, "bull": 255},
                            "as_of": "2026-08-28",
                            "sources": [{"report_id": "avav-buy", "title": "AVAV 买卖点笔记", "date": "2026-08-10"}],
                            "status": "current",
                        },
                        {
                            "code": "3110",
                            "aliases": ["3110"],
                            "company": "日东纺",
                            "market": "日股",
                            "currency": "JPY",
                            "bands": {"b1": [2550, 2700], "b2": [2050, 2350]},
                            "scenarios": {"bear": 2000, "base": 3650, "bull": 6000},
                            "as_of": "2026-08-28",
                            "sources": [],
                            "status": "current",
                        },
                        {"code": "SECRET", "aliases": [], "company": "不应返回的公司", "bands": {}, "scenarios": {}},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        Path(server.POINTS).write_text(
            json.dumps(
                {
                    "buy": {
                        "AVAV": "150/120/100 倒金字塔（财多多）",
                        "ONLYC": "330-350/270-290",
                        "RAW": "估值合适时再看，不给固定数字",
                    },
                    "sell": {},
                    "tgt": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        Path(server.LIVE_QUOTES).write_text(
            json.dumps({"t": int(time.time()), "src": "fixture", "q": {"AVAV": {"p": 147.94, "c": 1.2}}}),
            encoding="utf-8",
        )
        Path(server.STATIC_QUOTES).write_text(
            json.dumps(
                {
                    "updated": "2026-08-29 20:16 北京时间",
                    "sections": [
                        {
                            "sec": "test",
                            "rows": [
                                ["AeroVironment", "AVAV", "美股", "$76.8亿", "$409.83", "$152.28", -62.8, None, None, None, None, None, "", None, None, None, -1.4],
                                ["日东纺织", "3110", "日股", "¥5524.1亿", "¥6,390.00", "¥3,035.00", -52.5, None, None, None, None, None, "", None, None, None, 2.1],
                                ["仅财多多", "ONLYC", "美股", "$10亿", "$500.00", "$369.00", -26.2, None, None, None, None, None, "", None, None, None, 0.5],
                                ["待人工判断", "RAW", "A股", "¥10亿", "¥30.00", "¥20.00", -33.3, None, None, None, None, None, "", None, None, None, -0.5],
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        server.init()
        c = server.db()
        c.execute("INSERT INTO invites(code,created,scope) VALUES('KEDU-ONLY','now','kedu_points')")
        c.execute("INSERT INTO invites(code,created,scope) VALUES('STOCK-ONLY','now','stock_member')")
        c.commit(); c.close()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(timeout=2)
        (
            server.DB,
            server.POINTS,
            server.KEDU_POINTS,
            server.LIVE_QUOTES,
            server.STATIC_QUOTES,
            server.KEDU_ENABLED,
            server.KEDU_TABLE_ENABLED,
            server.hpw,
        ) = self.old
        self.tmp.cleanup()

    def request(self, path, method="GET", body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Origin": "https://lab.ziyuanai.top"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            response = exc
        return response.status, json.loads(response.read()), response.headers

    def register(self, invite, username):
        status, data, _ = self.request(
            "/api/register",
            "POST",
            {"invite": invite, "username": username, "password": "safe-pass", "scope": "stock_member", "role": "admin"},
        )
        self.assertEqual(status, 200)
        return data

    def test_kedu_permission_is_server_selected_and_point_is_single_company(self):
        status, config, headers = self.request("/api/kedu/config")
        self.assertEqual((status, config["enabled"]), (200, True))
        self.assertEqual(config["points_table_enabled"], True)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "https://lab.ziyuanai.top")
        self.assertEqual(self.request("/api/kedu/point?code=AVAV")[0], 401)

        account = self.register("KEDU-ONLY", "kedu-user")
        self.assertEqual(account["permissions"], ["kedu_points"])
        status, data, _ = self.request("/api/kedu/point?code=avav", token=account["token"])
        self.assertEqual(status, 200)
        self.assertEqual(data["item"]["company"], "AeroVironment")
        self.assertNotIn("不应返回的公司", json.dumps(data, ensure_ascii=False))
        self.assertEqual(data["item"]["live"]["state"], "inside_b2")
        self.assertEqual(data["item"]["live"]["bands"]["b1"]["distance_pct"], [6.8, 14.9])
        self.assertEqual(data["item"]["live"]["scenarios"]["bull"]["return_from_price_pct"], 72.4)
        self.assertEqual(data["item"]["live"]["price_mode"], "extended")
        self.assertEqual(self.request("/api/points", token=account["token"])[0], 403)

        # 日股不在美股盘前/盘后文件里，必须回退到全市场最新收盘价，不能显示成 0。
        status, data, _ = self.request("/api/kedu/point?code=3110", token=account["token"])
        self.assertEqual(status, 200)
        self.assertEqual(data["item"]["live"]["price"], 3035.0)
        self.assertEqual(data["item"]["live"]["price_mode"], "close")
        self.assertEqual(data["item"]["live"]["updated_at"], "2026-08-29 20:16 北京时间")

        # 盘前/盘后文件过期后不能一直覆盖更新得更晚的收盘价。
        Path(server.LIVE_QUOTES).write_text(
            json.dumps({"t": 1, "src": "stale", "q": {"AVAV": {"p": 147.94, "c": 1.2}}}),
            encoding="utf-8",
        )
        status, data, _ = self.request("/api/kedu/point?code=AVAV", token=account["token"])
        self.assertEqual(status, 200)
        self.assertEqual(data["item"]["live"]["price"], 152.28)
        self.assertEqual(data["item"]["live"]["price_mode"], "close")

    def test_stock_permission_cannot_read_kedu_points(self):
        account = self.register("STOCK-ONLY", "stock-user")
        self.assertEqual(account["permissions"], ["stock_member"])
        self.assertEqual(self.request("/api/kedu/point?code=AVAV", token=account["token"])[0], 403)
        self.assertEqual(self.request("/api/kedu/points", token=account["token"])[0], 403)

    def test_caido_parse_is_conservative_and_sources_stay_separate(self):
        parsed = server.parse_observation_text("150/120/100 倒金字塔（财多多）")
        self.assertEqual(parsed["bands"], [[150.0, 150.0], [120.0, 120.0], [100.0, 100.0]])
        self.assertEqual(parsed["threshold"], 150.0)
        self.assertEqual(parsed["parse_status"], "parsed")

        raw = server.parse_observation_text("估值合适时再看，不给固定数字")
        self.assertEqual(raw["bands"], [])
        self.assertIsNone(raw["threshold"])
        self.assertEqual(raw["parse_status"], "raw_only")

        items = {item["code"]: item for item in server.aggregate_kedu_points()}
        self.assertEqual(set(items), {"AVAV", "3110", "ONLYC", "RAW"})
        avav = items["AVAV"]
        self.assertEqual(avav["sources"]["caiduoduo"]["bands"][0], [150.0, 150.0])
        self.assertEqual(avav["sources"]["kedu"]["bands"]["b1"], [158, 170])
        self.assertNotIn("bands", avav)
        self.assertEqual(avav["relation"], "near")
        self.assertIsNone(items["ONLYC"]["sources"]["kedu"])
        self.assertEqual(items["RAW"]["relation"], "uncomparable")

    def test_legacy_direct_buypoints_shape_is_supported(self):
        Path(server.POINTS).write_text(json.dumps({"AVAV": "150/120/100"}), encoding="utf-8")
        points = server.load_points()
        self.assertEqual(points["buy"], {"AVAV": "150/120/100"})
        self.assertEqual(points["sell"], {})

    def test_kedu_points_table_requires_kedu_permission(self):
        self.assertEqual(self.request("/api/kedu/points")[0], 401)
        account = self.register("KEDU-ONLY", "kedu-table-user")
        status, data, headers = self.request("/api/kedu/points", token=account["token"])
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual({item["code"] for item in data["items"]}, {"AVAV", "3110", "ONLYC", "RAW"})
        self.assertNotIn("不应返回的公司", json.dumps(data, ensure_ascii=False))

    def test_feature_off_hides_endpoint(self):
        server.KEDU_ENABLED = False
        status, config, _ = self.request("/api/kedu/config")
        self.assertEqual((status, config["enabled"]), (200, False))
        self.assertEqual(self.request("/api/kedu/point?code=AVAV")[0], 404)

    def test_table_feature_off_does_not_break_single_company_points(self):
        account = self.register("KEDU-ONLY", "kedu-single-user")
        server.KEDU_TABLE_ENABLED = False
        status, config, _ = self.request("/api/kedu/config")
        self.assertEqual(config["points_table_enabled"], False)
        self.assertEqual(self.request("/api/kedu/points", token=account["token"])[0], 404)
        self.assertEqual(self.request("/api/kedu/point?code=AVAV", token=account["token"])[0], 200)


if __name__ == "__main__":
    unittest.main()
