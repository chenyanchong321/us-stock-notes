#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

REPO_URL="https://github.com/chenyanchong321/us-stock-notes.git"
SITE_DIR="/var/www/us-stock"
DOMAIN="stock.ziyuanai.top"

echo "== packages =="
apt-get update -qq
apt-get install -y -qq nginx git curl python3 ca-certificates cron

echo "== sync site =="
if [ -d "$SITE_DIR/.git" ]; then
  git -C "$SITE_DIR" remote set-url origin "$REPO_URL"
  git -C "$SITE_DIR" fetch --depth 1 origin main
  git -C "$SITE_DIR" reset --hard origin/main
else
  rm -rf "$SITE_DIR"
  git clone --depth 1 "$REPO_URL" "$SITE_DIR"
fi

mkdir -p "$SITE_DIR/data"
grep -qxF "data/live.json" "$SITE_DIR/.git/info/exclude" 2>/dev/null || echo "data/live.json" >> "$SITE_DIR/.git/info/exclude"

echo "== nginx =="
install -d /etc/nginx/sites-available /etc/nginx/sites-enabled
cat > /etc/nginx/sites-available/us-stock <<'NGINX'
server {
    listen 80;
    server_name stock.ziyuanai.top 118.31.109.150;
    root /var/www/us-stock;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }

    location = /data/live.json {
        add_header Cache-Control "no-store" always;
        try_files $uri =404;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/us-stock /etc/nginx/sites-enabled/us-stock
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx >/dev/null
systemctl restart nginx

echo "== live quote script =="
cat > /root/us-live.py <<'PY'
#!/usr/bin/env python3
import json
import pathlib
import sys
import time
import urllib.request

site_dir = pathlib.Path("/var/www/us-stock")
out = site_dir / "data" / "live.json"
watchlist = site_dir / "config" / "watchlist.json"
out.parent.mkdir(parents=True, exist_ok=True)

try:
    data = json.loads(watchlist.read_text(encoding="utf-8"))
except Exception as exc:
    out.write_text(json.dumps({"t": 0, "q": {}, "error": str(exc)}, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.exit(0)

codes = []
for section in data.get("sections", []):
    for item in section.get("items", []):
        market = str(item.get("market", ""))
        code = str(item.get("code", "")).strip()
        if market.startswith("\u7f8e\u80a1") and code.isalpha():
            codes.append("gb_" + code.lower())

codes = list(dict.fromkeys(codes))
if not codes:
    out.write_text('{"t":0,"q":{}}\n', encoding="utf-8")
    sys.exit(0)

url = "https://hq.sinajs.cn/list=" + ",".join(codes)
req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"})

raw = b""
try:
    with urllib.request.urlopen(req, timeout=12) as resp:
        raw = resp.read()
except Exception:
    raw = b""

text = raw.decode("gbk", errors="ignore")
quotes = {}
for line in text.splitlines():
    if "hq_str_gb_" not in line:
        continue
    try:
        code = line.split("hq_str_gb_", 1)[1].split("=", 1)[0].strip().upper()
        fields = line.split('"', 2)[1].split(",")
        price = float(fields[1])
        pct = float(fields[2])
    except Exception:
        continue
    if price > 0:
        quotes[code] = {"p": round(price, 2), "c": round(pct, 2)}

tmp = out.with_suffix(".json.tmp")
tmp.write_text(json.dumps({"t": int(time.time()), "q": quotes}, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
tmp.replace(out)
PY
chmod 700 /root/us-live.py

cat > /root/us-live.sh <<'LIVE'
#!/usr/bin/env bash
set -euo pipefail
/usr/bin/python3 /root/us-live.py
LIVE
chmod 700 /root/us-live.sh
/root/us-live.sh || true

echo "== cron =="
(crontab -l 2>/dev/null | grep -v 'us-live\|us-stock-sync' || true
 echo '* * * * * /root/us-live.sh  # us-live'
 echo '*/2 * * * * cd /var/www/us-stock && git pull -q origin main >> /root/us-sync.log 2>&1  # us-stock-sync') | crontab -
systemctl enable cron >/dev/null || true
systemctl restart cron || true

echo "== verify =="
nginx -t
printf 'nginx='
systemctl is-active nginx
printf 'cron='
systemctl is-active cron || true
printf 'site_head='
git -C "$SITE_DIR" rev-parse --short HEAD
ls -l "$SITE_DIR/index.html" "$SITE_DIR/data/live.json"
crontab -l
curl -sI -H "Host: $DOMAIN" http://127.0.0.1/ | sed -n '1,8p'
printf 'live_sample='
head -c 700 "$SITE_DIR/data/live.json"
echo
echo "DEPLOY_OK"
